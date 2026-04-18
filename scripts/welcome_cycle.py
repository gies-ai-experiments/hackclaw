"""Hourly welcome cron.

For each interest-form row that hasn't been welcomed yet:
- Gies-eligible + not applied → send welcome (with application link)
- Non-Gies + not applied     → send not-eligible (share with a friend)
- Already applied            → stamp welcome col anyway so we never re-welcome

One-shot per person. Stamps col L (Welcome Sent At) after send.

Env vars: GOOGLE_SERVICE_ACCOUNT_JSON, INTEREST_SHEET_ID, INTEREST_SHEET_GID,
          GIES_SHEET_ID, SMTP_HOST/PORT/USER/PASS/FROM, REMINDER_DEADLINE.
"""

from __future__ import annotations

import os
import smtplib
import ssl
import sys
from datetime import datetime
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

COL_WELCOME_SENT_AT = 12  # L (1-based for gspread)


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
        trimmed = r[1:] if r else r  # drop Timestamp col
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

    smtp_host = _env("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = _env("SMTP_USER")
    smtp_pass = _env("SMTP_PASS")
    smtp_from = os.environ.get("SMTP_FROM") or smtp_user

    client = open_client(sa)
    iws = open_worksheet_by_gid(client, interest_id, interest_gid)
    aws = open_first_worksheet(client, app_id)

    tpl_dir = Path(__file__).resolve().parent.parent / "nanobot/onboard"
    welcome_tpl = load_template(tpl_dir / "welcome_email_template.txt")
    not_eligible_tpl = load_template(tpl_dir / "not_eligible_email_template.txt")

    app_rows = fetch_rows(aws)
    applied = _build_applied(app_rows)
    logger.info("applied set size: {}", len(applied))

    rows = fetch_rows(iws)
    logger.info("interest rows: {}", len(rows))

    now_iso = datetime.now().isoformat(timespec="seconds")

    # Open SMTP once
    sent_count = 0
    skipped = 0
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as conn:
        conn.starttls(context=ssl.create_default_context())
        conn.login(smtp_user, smtp_pass)

        for idx, r in enumerate(rows, start=1):
            name = _cell(r, 1)
            email = _cell(r, 2)
            program = _cell(r, 5)
            welcome_sent_at = _cell(r, 11)  # col L (0-indexed = 11)

            canon = canonical_illinois_email(email)
            if canon is None:
                skipped += 1
                continue
            if welcome_sent_at:
                skipped += 1
                continue

            sheet_row = idx + 1  # header is row 1
            if canon in applied:
                # Already applied; still stamp so we never welcome them.
                iws.update_cell(sheet_row, COL_WELCOME_SENT_AT, now_iso)
                skipped += 1
                continue

            tpl = welcome_tpl if is_gies_program(program) else not_eligible_tpl
            rendered = render(tpl, name=name or "there")

            msg = EmailMessage()
            msg["From"] = smtp_from
            msg["To"] = canon
            msg["Subject"] = rendered.subject
            msg.set_content(rendered.body)

            try:
                conn.send_message(msg)
                iws.update_cell(sheet_row, COL_WELCOME_SENT_AT, now_iso)
                sent_count += 1
                logger.info(
                    "welcome → {} ({} / row {})",
                    canon,
                    "gies" if is_gies_program(program) else "not-eligible",
                    sheet_row,
                )
            except Exception as exc:
                logger.error("send failed → {} (row {}): {}", canon, sheet_row, exc)

    logger.info("Welcome cycle done. sent={} skipped={}", sent_count, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
