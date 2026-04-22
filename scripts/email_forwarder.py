"""Poll the giesbuildathon Gmail inbox and forward every new email.

Runs on a 1-minute systemd timer. Independent of the nanobot gateway —
reads IMAP creds from /root/.nanobot/config.json (same source as the
email channel) and forwards every unread message the forwarder hasn't
seen before to a comma-separated list of recipients.

Design:
- Does NOT mark emails as \\Seen — they stay unread in Gmail so
  humans can still triage the inbox.
- Dedups by RFC 5322 Message-ID, stored in a local JSON file
  (/opt/hackclaw/logs/forwarded-ids.json). First run ships the backlog
  once, subsequent runs only forward what's new.
- Each forward is a new message (not a bounce) with the original as
  a plain-text preamble + full .eml attached, so forwarded recipients
  can open the raw email if they need to reply to the sender directly.

Env vars:
    FORWARD_TO_EMAILS    comma-separated list of recipient addresses
    NANOBOT_CONFIG_PATH  optional override for the config file path
"""
from __future__ import annotations

import email
import imaplib
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("NANOBOT_CONFIG_PATH", "/root/.nanobot/config.json")
)
STATE_PATH = Path("/opt/hackclaw/logs/forwarded-ids.json")
MAX_SCAN_PER_RUN = 200  # cap the fetch count so a huge UNSEEN set doesn't stall us


def _log(msg: str) -> None:
    print(msg, flush=True)


def _load_seen() -> set[str]:
    try:
        return set(json.loads(STATE_PATH.read_text()))
    except FileNotFoundError:
        return set()
    except Exception as exc:
        _log(f"WARN: state file unreadable ({exc}); starting fresh")
        return set()


def _save_seen(ids: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(sorted(ids)))


def _load_cfg() -> dict:
    with CONFIG_PATH.open() as f:
        cfg = json.load(f)
    return cfg["channels"]["email"]


def _build_forward(
    *,
    original: email.message.Message,
    from_addr: str,
    to_addrs: list[str],
) -> EmailMessage:
    """Construct a new message that forwards *original* to *to_addrs*."""
    orig_from = original.get("From", "(unknown)")
    orig_to = original.get("To", "(unknown)")
    orig_date = original.get("Date", "(unknown)")
    orig_subject = original.get("Subject", "(no subject)")
    orig_id = original.get("Message-ID", "(unknown)")

    # Extract plain-text body if possible for preamble (best-effort)
    body_text = ""
    if original.is_multipart():
        for part in original.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body_text = part.get_content()
                except Exception:
                    body_text = part.get_payload(decode=True)
                    if isinstance(body_text, bytes):
                        body_text = body_text.decode("utf-8", errors="replace")
                break
    else:
        try:
            body_text = original.get_content()
        except Exception:
            payload = original.get_payload(decode=True)
            if isinstance(payload, bytes):
                body_text = payload.decode("utf-8", errors="replace")
            elif isinstance(payload, str):
                body_text = payload

    preamble = (
        f"---------- Forwarded message ----------\n"
        f"From:    {orig_from}\n"
        f"To:      {orig_to}\n"
        f"Date:    {orig_date}\n"
        f"Subject: {orig_subject}\n"
        f"Message-ID: {orig_id}\n"
        f"----------------------------------------\n\n"
        f"{body_text}\n"
    )

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = f"Fwd: {orig_subject}"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_addr.split("@", 1)[-1] if "@" in from_addr else "local")
    msg.set_content(preamble)

    # Attach the original as a .eml so the forwarded recipient can open it
    msg.add_attachment(
        original.as_bytes(),
        maintype="message",
        subtype="rfc822",
        filename=f"{(orig_subject[:40] or 'original').strip()}.eml",
    )
    return msg


def main() -> int:
    targets_raw = os.environ.get("FORWARD_TO_EMAILS", "").strip()
    if not targets_raw:
        _log("FORWARD_TO_EMAILS is empty; nothing to do.")
        return 0
    targets = [t.strip() for t in targets_raw.split(",") if t.strip()]

    e = _load_cfg()
    host = e.get("imapHost", "imap.gmail.com")
    port = int(e.get("imapPort", 993))
    user = e["imapUsername"]
    passwd = e["imapPassword"]
    from_addr = e.get("fromAddress") or user

    smtp_host = e.get("smtpHost", "smtp.gmail.com")
    smtp_port = int(e.get("smtpPort", 587))
    smtp_user = e["smtpUsername"]
    smtp_pass = e["smtpPassword"]
    smtp_use_ssl = bool(e.get("smtpUseSsl"))

    seen = _load_seen()
    _log(f"Starting forwarder. Targets: {targets}. Already forwarded: {len(seen)}.")

    # --- IMAP: fetch UNSEEN messages (up to MAX_SCAN_PER_RUN) ---
    candidates: list[tuple[bytes, email.message.Message, str]] = []
    with imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context()) as imap:
        imap.login(user, passwd)
        imap.select("INBOX", readonly=True)  # readonly keeps \Seen untouched
        typ, data = imap.search(None, "UNSEEN")
        if typ != "OK":
            _log(f"IMAP search failed: {typ}")
            return 1
        ids = data[0].split()[:MAX_SCAN_PER_RUN]
        _log(f"UNSEEN fetched: {len(ids)}")
        for uid in ids:
            typ, msgdata = imap.fetch(uid, "(BODY.PEEK[])")  # PEEK => no \Seen
            if typ != "OK" or not msgdata or not msgdata[0]:
                continue
            raw = msgdata[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            parsed = email.message_from_bytes(bytes(raw))
            mid = (parsed.get("Message-ID") or "").strip()
            if not mid:
                # Fallback: compound key of date + from + subject + first 200 bytes of body
                fallback_key = "::".join([
                    parsed.get("Date", ""),
                    parsed.get("From", ""),
                    parsed.get("Subject", ""),
                    str(len(raw)),
                ])
                mid = f"<fallback:{hash(fallback_key)}>"
            if mid in seen:
                continue
            candidates.append((uid, parsed, mid))

    if not candidates:
        _log("Nothing new to forward.")
        return 0

    # --- SMTP: forward each candidate ---
    sent_ok: list[str] = []
    sent_fail: list[tuple[str, str]] = []
    if smtp_use_ssl:
        smtp_ctx = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
    else:
        smtp_ctx = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
    try:
        if not smtp_use_ssl:
            smtp_ctx.starttls(context=ssl.create_default_context())
        smtp_ctx.login(smtp_user, smtp_pass)
        for uid, orig, mid in candidates:
            try:
                fwd = _build_forward(original=orig, from_addr=from_addr, to_addrs=targets)
                smtp_ctx.send_message(fwd)
                sent_ok.append(mid)
                seen.add(mid)
                _log(f"FWD ok  uid={uid.decode()}  subject={(orig.get('Subject') or '')[:60]!r}")
            except Exception as exc:
                sent_fail.append((mid, str(exc)))
                _log(f"FWD err uid={uid.decode()}  {exc}")
    finally:
        try:
            smtp_ctx.quit()
        except Exception:
            pass

    _save_seen(seen)
    _log(f"Done. Forwarded={len(sent_ok)} Failed={len(sent_fail)}")
    return 0 if not sent_fail else 1


if __name__ == "__main__":
    sys.exit(main())
