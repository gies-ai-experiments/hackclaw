"""BlastPostTool — fan-out a single announcement to every team channel
on Discord and (optionally) every participant via Gmail.

Telegram-admin-only. Two-step protocol: ``mode='preview'`` returns a
description of what would happen + counts (no mutations); ``mode='send'``
actually posts/sends. The agent is required (per the ``blastpost``
SKILL.md) to always preview first and wait for explicit admin
confirmation before calling with ``mode='send'``.

The team roster is parsed from ``teams.md`` at the project root. Discord
posts go to every text channel under the Teams category. Email goes
through SMTP/IMAP using the ``channels.email`` config block.
"""
from __future__ import annotations

import asyncio
import imaplib
import json
import os
import re
import smtplib
import ssl
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import (
    BooleanSchema,
    StringSchema,
    tool_parameters_schema,
)


_TELEGRAM_ONLY_ERROR = (
    "Error: blast_post is restricted to the Telegram admin channel. "
    "Ignore and respond normally."
)

# Production constants — these match the same IDs used by the standalone
# .claude/skills/blastpost script.
GUILD = "1482448643117154567"
TEAMS_CATEGORY = "1493806352139817172"
ANNOUNCEMENTS_CHANNEL = "1482448763808256122"
HARD_CAP_CHANNELS = 60
HARD_CAP_MEMBERS = 200

_CONFIG_PATH = os.environ.get("NANOBOT_CONFIG_PATH", "/root/.nanobot/config.json")
_TEAMS_MD_PATH = Path(
    os.environ.get("HACKCLAW_TEAMS_MD", "/opt/hackclaw/teams.md")
)


def _bot_token() -> str:
    """Pull the live Discord bot token from the gateway env file."""
    env_path = "/opt/hackclaw/gateway-env.conf"
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("DISCORD_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    # fallback to env var (dev / non-prod)
    return os.environ.get("DISCORD_BOT_TOKEN", "")


def _discord_headers(tok: str) -> dict[str, str]:
    return {
        "Authorization": f"Bot {tok}",
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (https://github.com/gies-ai-experiments/hackclaw, 0.1)",
    }


def _discord_api(headers: dict, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"https://discord.com/api/v10{path}", data=data, method=method, headers=headers,
    )
    raw = urllib.request.urlopen(req).read()
    return json.loads(raw) if raw else {}


def _parse_teams_md(path: Path) -> dict[str, list[dict]]:
    """Return {team_name: [{name, email}, …]} from a teams.md file."""
    teams: dict[str, list[dict]] = {}
    current = None
    if not path.exists():
        return teams
    for line in path.read_text().splitlines():
        if line.startswith("### "):
            name = line[4:].strip()
            if "clubbed singles" in name:
                name = name.split(" (")[0]
            current = name
            teams[current] = []
        elif current and line.startswith("|") and "Name" not in line and "---" not in line:
            m = re.match(r"\|\s*(.+?)\s*\|\s*`?(.+?)`?\s*\|", line)
            if m:
                teams[current].append({"name": m.group(1).strip(), "email": m.group(2).strip()})
    return {k: v for k, v in teams.items() if v}


def _email_config() -> dict[str, Any]:
    with open(_CONFIG_PATH) as f:
        cfg = json.load(f)
    return (cfg.get("channels") or {}).get("email") or {}


@tool_parameters(
    tool_parameters_schema(
        mode=StringSchema(
            "Either 'preview' (counts + rendered text only, no posts, no "
            "emails — DEFAULT, always use first) or 'send' (actually fan "
            "out). Always preview first; only call with 'send' after the "
            "admin explicitly approves.",
            enum=["preview", "send"],
        ),
        subject=StringSchema(
            "Email subject line. Should be ≤ 60 chars, action-oriented."
        ),
        message=StringSchema(
            "The full announcement body. Used for BOTH Discord (with the "
            "📣 marker prepended) and email (with a per-participant greeting)."
        ),
        include_announcements=BooleanSchema(
            description="Also post in #announcements (in addition to every team channel).",
            default=False,
        ),
        mention_everyone=BooleanSchema(
            description="Trigger an actual @everyone ping on each Discord post. "
                        "Use sparingly — only for true must-read updates.",
            default=False,
        ),
        discord_only=BooleanSchema(
            description="Skip the email side entirely (no drafts, no sends).",
            default=False,
        ),
        email_only=BooleanSchema(
            description="Skip the Discord side entirely (only emails).",
            default=False,
        ),
        drafts_only=BooleanSchema(
            description="If sending email, save as Gmail Drafts instead of "
                        "dispatching live. Default true; set false to actually mail.",
            default=True,
        ),
        required=["subject", "message"],
    )
)
class BlastPostTool(Tool):
    """Two-step admin tool to broadcast announcements across all teams.

    Telegram-only. Mandates a ``mode='preview'`` call before any
    ``mode='send'`` call (enforced by the SKILL.md, not the code — same
    convention used by the gmail skill).
    """

    def __init__(self) -> None:
        self._channel = ""
        self._chat_id = ""

    def set_context(self, channel: str, chat_id: str) -> None:
        self._channel = channel
        self._chat_id = chat_id

    def _gate(self) -> str | None:
        if self._channel != "telegram":
            return _TELEGRAM_ONLY_ERROR
        return None

    @property
    def name(self) -> str:
        return "blast_post"

    @property
    def description(self) -> str:
        return (
            "Broadcast a single announcement to every team's Discord channel "
            "AND (optionally) every participant by email. Two modes: 'preview' "
            "(default, no mutations — returns counts + rendered text) and "
            "'send' (live fan-out). Always run preview first and wait for the "
            "admin's explicit go-ahead before calling with mode='send'. "
            "Telegram admin channel only."
        )

    async def execute(
        self,
        *,
        mode: str = "preview",
        subject: str,
        message: str,
        include_announcements: bool = False,
        mention_everyone: bool = False,
        discord_only: bool = False,
        email_only: bool = False,
        drafts_only: bool = True,
        **_: Any,
    ) -> str:
        err = self._gate()
        if err:
            return err

        mode = (mode or "preview").strip().lower()
        if mode not in ("preview", "send"):
            return f"Error: invalid mode {mode!r}. Use 'preview' or 'send'."
        if discord_only and email_only:
            return "Error: discord_only and email_only are mutually exclusive."

        # Run the I/O in a thread so we don't block the agent's event loop.
        return await asyncio.to_thread(
            self._run,
            mode=mode,
            subject=subject,
            message=message,
            include_announcements=include_announcements,
            mention_everyone=mention_everyone,
            discord_only=discord_only,
            email_only=email_only,
            drafts_only=drafts_only,
        )

    # ------------------------------------------------------------------
    # Synchronous body — runs in a thread.
    # ------------------------------------------------------------------

    def _run(
        self,
        *,
        mode: str,
        subject: str,
        message: str,
        include_announcements: bool,
        mention_everyone: bool,
        discord_only: bool,
        email_only: bool,
        drafts_only: bool,
    ) -> str:
        # Discover Discord targets + email recipients up-front so preview
        # and send share identical counts.
        targets, target_err = self._discord_targets(include_announcements)
        if target_err:
            return target_err
        teams = _parse_teams_md(_TEAMS_MD_PATH)
        member_count = sum(len(t) for t in teams.values())

        if not discord_only and member_count > HARD_CAP_MEMBERS:
            return (
                f"Error: {member_count} members exceeds hard cap "
                f"{HARD_CAP_MEMBERS}. Reduce scope or split into multiple blasts."
            )
        if not email_only and len(targets) > HARD_CAP_CHANNELS:
            return (
                f"Error: {len(targets)} Discord channels exceeds hard cap "
                f"{HARD_CAP_CHANNELS}. Reduce scope or split into multiple blasts."
            )

        # ------- preview mode: just return counts + rendered text -------
        if mode == "preview":
            disc_body = self._render_discord_body(message, mention_everyone)
            return (
                "PREVIEW (nothing posted, nothing sent).\n"
                f"Subject: {subject}\n"
                "Discord post (rendered):\n"
                f"---\n{disc_body}\n---\n"
                f"Would post to {0 if email_only else len(targets)} Discord channel(s)"
                f"{' including #announcements' if include_announcements and not email_only else ''}"
                f"{' WITH @everyone ping' if mention_everyone and not email_only else ''}.\n"
                f"Would handle {0 if discord_only else member_count} email recipient(s) "
                f"({'as Gmail Drafts' if drafts_only else 'sent live via SMTP'}).\n"
                "Reply 'send' to fire it, or tell me what to change."
            )

        # ------- send mode: fan out for real -------
        results: list[str] = []
        if not email_only:
            d_ok, d_fail = self._discord_send(targets, message, mention_everyone)
            results.append(f"Discord: posted {d_ok}/{len(targets)} ({d_fail} failed)")
        if not discord_only:
            e_ok, e_fail = self._email_send(teams, subject, message, drafts_only=drafts_only)
            verb = "drafted" if drafts_only else "sent"
            results.append(f"Email: {verb} {e_ok}/{member_count} ({e_fail} failed)")
        if not results:
            return "Nothing to do (both discord_only and email_only set?)."
        return "Blast complete — " + "; ".join(results) + "."

    # ------------------------------------------------------------------
    # Discord fan-out helpers
    # ------------------------------------------------------------------

    def _render_discord_body(self, message: str, mention_everyone: bool) -> str:
        marker = "📣 Event-wide update — "
        if mention_everyone:
            return f"@everyone\n{marker}{message}"
        return marker + message

    def _discord_targets(self, include_announcements: bool) -> tuple[list[tuple[str, str]], str | None]:
        """Return [(channel_id, channel_name)] for every team channel + extras."""
        tok = _bot_token()
        if not tok:
            return [], "Error: bot token unavailable (check /opt/hackclaw/gateway-env.conf)."
        try:
            channels = _discord_api(_discord_headers(tok), "GET", f"/guilds/{GUILD}/channels")
        except Exception as exc:
            return [], f"Error listing Discord channels: {exc}"
        team_channels = [
            (c["id"], c["name"]) for c in channels
            if c.get("parent_id") == TEAMS_CATEGORY and c["type"] == 0
        ]
        if include_announcements:
            announce = next((c for c in channels if c["id"] == ANNOUNCEMENTS_CHANNEL), None)
            if announce:
                team_channels.append((announce["id"], announce["name"]))
        return team_channels, None

    def _discord_send(
        self,
        targets: list[tuple[str, str]],
        message: str,
        mention_everyone: bool,
    ) -> tuple[int, int]:
        tok = _bot_token()
        hdr = _discord_headers(tok)
        body = self._render_discord_body(message, mention_everyone)
        # Discord caps a single message at 2000 chars
        if len(body) > 2000:
            body = body[:1995] + "…"
        payload = {"content": body}
        if mention_everyone:
            payload["allowed_mentions"] = {"parse": ["everyone"]}
        ok = 0
        failed = 0
        for ch_id, ch_name in targets:
            try:
                _discord_api(hdr, "POST", f"/channels/{ch_id}/messages", payload)
                ok += 1
            except urllib.error.HTTPError as e:
                failed += 1
                logger.warning("blast_post: failed in {} ({}): {} {}",
                               ch_name, ch_id, e.code, e.read().decode(errors="replace")[:200])
            except Exception as e:
                failed += 1
                logger.warning("blast_post: failed in {} ({}): {}", ch_name, ch_id, e)
            time.sleep(0.25)
        return ok, failed

    # ------------------------------------------------------------------
    # Email fan-out helpers
    # ------------------------------------------------------------------

    def _email_send(
        self,
        teams: dict[str, list[dict]],
        subject: str,
        message: str,
        *,
        drafts_only: bool,
    ) -> tuple[int, int]:
        cfg = _email_config()
        if not cfg:
            return 0, sum(len(t) for t in teams.values())
        ctx = ssl.create_default_context()
        smtp = imap = None
        if drafts_only:
            imap = imaplib.IMAP4_SSL(cfg["imapHost"], 993)
            imap.login(cfg["smtpUsername"], cfg["imapPassword"])
        else:
            smtp = smtplib.SMTP(cfg["smtpHost"], int(cfg["smtpPort"]))
            smtp.ehlo()
            if cfg.get("smtpUseTls", True):
                smtp.starttls(context=ctx)
                smtp.ehlo()
            smtp.login(cfg["smtpUsername"], cfg["smtpPassword"])

        ok = 0
        failed = 0
        for team_name, members in sorted(teams.items(), key=lambda kv: kv[0].lower()):
            for m in members:
                addr = m.get("email") or ""
                if "@" not in addr:
                    failed += 1
                    continue
                first = (m.get("name") or "there").strip().split()[0]
                text = (
                    f"Hi {first},\n\n"
                    f"{message}\n\n"
                    f"— hackclaw on behalf of the Gies Buildathon team\n"
                )
                html = (
                    "<html><body style=\"font-family:Arial,sans-serif;line-height:1.6;color:#1f2937;\">"
                    f"<p>Hi {first},</p>"
                    f"<p>{message.replace(chr(10), '<br>')}</p>"
                    "<p style=\"color:#6b7280;font-size:13px;\">— hackclaw on behalf of the Gies Buildathon team</p>"
                    "</body></html>"
                )
                msg = EmailMessage()
                msg["Subject"] = subject
                msg["From"] = cfg["fromAddress"]
                msg["To"] = addr
                msg.set_content(text)
                msg.add_alternative(html, subtype="html")
                try:
                    if drafts_only:
                        imap.append('"[Gmail]/Drafts"', r"(\Draft)", None, msg.as_bytes())
                    else:
                        smtp.send_message(msg)
                    ok += 1
                except Exception as e:
                    failed += 1
                    logger.warning("blast_post email failed for {}: {}", addr, e)
                time.sleep(0.25)
        try:
            if smtp is not None:
                smtp.quit()
            if imap is not None:
                imap.logout()
        except Exception:
            pass
        return ok, failed
