"""Every-6h apply-reminder cron.

For each Gies-eligible interest-form row:
- Must have been welcomed (col L non-empty) — the hourly welcome cron handles first-touch.
- Must not be in the applied set.
- Reminder count < REMINDER_MAX_COUNT (default 3).
- Last reminder at least REMINDER_MIN_GAP_HOURS ago (default 6).

Stamps col M (Reminder Count += 1) and col N (Last Reminder At = now) after each send.

Env: GOOGLE_SERVICE_ACCOUNT_JSON, INTEREST_SHEET_ID, INTEREST_SHEET_GID,
     GIES_SHEET_ID, SMTP_*, REMINDER_DEADLINE, REMINDER_MAX_COUNT,
     REMINDER_MIN_GAP_HOURS.
"""

from __future__ import annotations

import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from loguru import logger

from nanobot.onboard.email_canonical import canonical_illinois_email
from nanobot.onboard.parser import extract_members, is_gies_program
from nanobot.onboard.sheet_io import (
    fetch_rows,
    open_client,
    open_first_worksheet,
    open_worksheet_by_gid,
)
from nanobot.onboard.templates import load_template, render

COL_REMINDER_COUNT = 13     # M
COL_LAST_REMINDER_AT = 14   # N


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        logger.error("missing env var: {}", name)
        sys.exit(2)
    return v


def _cell(row: list[str], idx: int) -> str:
    return (row[idx] or "").strip() if idx < len(row) else ""


def _build_applied(app_rows: list[list[str]]) -> set[str]:
    applied: set[str] = set()
    for r in app_rows:
        trimmed = r[1:] if r else r
        for m in extract_members(trimmed):
            canon = canonical_illinois_email(m.email)
            if canon:
                applied.add(canon)
    return applied


def main() -> int:
    deadline_str = os.environ.get("REMINDER_DEADLINE")
    if deadline_str and datetime.now() > datetime.fromisoformat(deadline_str):
        logger.info("past deadline {}; no-op", deadline_str)
        return 0

    sa = _env("GOOGLE_SERVICE_ACCOUNT_JSON")
    interest_id = _env("INTEREST_SHEET_ID")
    interest_gid = int(os.environ.get("INTEREST_SHEET_GID", "502554177"))
    app_id = _env("GIES_SHEET_ID")

    max_reminders = int(os.environ.get("REMINDER_MAX_COUNT", "3"))
    min_gap_hours = int(os.environ.get("REMINDER_MIN_GAP_HOURS", "6"))
    min_gap = timedelta(hours=min_gap_hours)

    smtp_host = _env("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = _env("SMTP_USER")
    smtp_pass = _env("SMTP_PASS")
    smtp_from = os.environ.get("SMTP_FROM") or smtp_user

    client = open_client(sa)
    iws = open_worksheet_by_gid(client, interest_id, interest_gid)
    aws = open_first_worksheet(client, app_id)

    tpl_dir = Path(__file__).resolve().parent.parent / "nanobot/onboard"
    reminder_tpl = load_template(tpl_dir / "reminder_email_template.txt")

    app_rows = fetch_rows(aws)
    applied = _build_applied(app_rows)
    logger.info("applied set size: {}", len(applied))

    rows = fetch_rows(iws)
    logger.info("interest rows: {}", len(rows))

    now = datetime.now()
    now_iso = now.isoformat(timespec="seconds")

    sent_count = 0
    skipped = 0
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as conn:
        conn.starttls(context=ssl.create_default_context())
        conn.login(smtp_user, smtp_pass)

        for idx, r in enumerate(rows, start=1):
            name = _cell(r, 1)
            email = _cell(r, 2)
            program = _cell(r, 5)
            welcome_sent_at = _cell(r, 11)  # L
            count_raw = _cell(r, 12)        # M
            last_reminder_at = _cell(r, 13) # N

            canon = canonical_illinois_email(email)
            if canon is None:
                skipped += 1
                continue
            if not is_gies_program(program):
                skipped += 1
                continue
            if canon in applied:
                skipped += 1
                continue
            if not welcome_sent_at:
                skipped += 1
                continue

            try:
                count = int(count_raw) if count_raw else 0
            except ValueError:
                logger.warning("row {}: bad count {!r}; treating as 0", idx + 1, count_raw)
                count = 0

            if count >= max_reminders:
                skipped += 1
                continue

            if last_reminder_at:
                try:
                    last = datetime.fromisoformat(last_reminder_at)
                    if now - last < min_gap:
                        skipped += 1
                        continue
                except ValueError:
                    logger.warning("row {}: bad last_reminder_at {!r}", idx + 1, last_reminder_at)

            sheet_row = idx + 1
            rendered = render(reminder_tpl, name=name or "there")

            msg = EmailMessage()
            msg["From"] = smtp_from
            msg["To"] = canon
            msg["Subject"] = rendered.subject
            msg.set_content(rendered.body)

            try:
                conn.send_message(msg)
                iws.update_cell(sheet_row, COL_REMINDER_COUNT, count + 1)
                iws.update_cell(sheet_row, COL_LAST_REMINDER_AT, now_iso)
                sent_count += 1
                logger.info(
                    "reminder #{} → {} (row {})", count + 1, canon, sheet_row
                )
            except Exception as exc:
                logger.error("send failed → {} (row {}): {}", canon, sheet_row, exc)

    logger.info("Reminder cycle done. sent={} skipped={}", sent_count, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
