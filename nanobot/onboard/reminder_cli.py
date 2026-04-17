"""CLI wrapper around the reminder poller with an interactive confirm step.

Usage:
    python -m nanobot.onboard.reminder_cli [--dry-run] \\
        --service-account PATH \\
        --interest-sheet ID --interest-gid GID \\
        --app-sheet ID \\
        --smtp-host HOST --smtp-port PORT \\
        --smtp-user USER --smtp-pass PASS \\
        [--from-address ADDR] [--smtp-ssl]

Reads both Google Sheets once, prints a per-row preview of what would
happen, prompts before sending anything, and then performs a single
poll cycle. No loop — this is the "review and fire once" tool.

Pass ``--dry-run`` to stop after the preview with no prompt and no
sends. Useful for sanity-checking sheet access and classification
without any risk of mailing anyone.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from loguru import logger

from nanobot.onboard.reminder_config import DEFAULT_INTEREST_GID, DEFAULT_MAX_REMINDERS
from nanobot.onboard.reminder_poller import (
    SMTPSettings,
    format_preview,
    preview_decisions,
    run_once,
    send_email,
)
from nanobot.onboard.sheet_io import (
    SheetWriter,
    fetch_rows,
    open_client,
    open_first_worksheet,
    open_worksheet_by_gid,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview and confirm the reminder poller before sending.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only; no prompt, no sends.")
    parser.add_argument("--service-account", required=True, help="Path to service-account JSON.")
    parser.add_argument("--interest-sheet", required=True, help="Interest form spreadsheet ID.")
    parser.add_argument(
        "--interest-gid",
        type=int,
        default=DEFAULT_INTEREST_GID,
        help=f"Interest tab gid (default {DEFAULT_INTEREST_GID}).",
    )
    parser.add_argument("--app-sheet", required=True, help="Application form spreadsheet ID.")
    parser.add_argument("--smtp-host", required=True)
    parser.add_argument("--smtp-port", type=int, default=587)
    parser.add_argument("--smtp-user", required=True)
    parser.add_argument("--smtp-pass", required=True)
    parser.add_argument("--from-address", default="", help="Defaults to --smtp-user.")
    parser.add_argument("--smtp-ssl", action="store_true", help="Use SMTPS (port 465) instead of STARTTLS.")
    parser.add_argument(
        "--max-reminders",
        type=int,
        default=DEFAULT_MAX_REMINDERS,
        help=f"Per-row reminder cap (default {DEFAULT_MAX_REMINDERS}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    logger.info("Connecting to Google Sheets...")
    client = open_client(args.service_account)
    interest_ws = open_worksheet_by_gid(client, args.interest_sheet, args.interest_gid)
    application_ws = open_first_worksheet(client, args.app_sheet)

    logger.info("Fetching rows...")
    interest_rows = fetch_rows(interest_ws)
    application_rows = fetch_rows(application_ws)

    decisions = preview_decisions(
        interest_rows=interest_rows,
        application_rows=application_rows,
        max_reminders=args.max_reminders,
    )

    print()
    print(format_preview(decisions))
    print()

    if args.dry_run:
        print("--dry-run: stopping before prompt. No emails sent.")
        return 0

    actionable_count = sum(1 for d in decisions if d.action.value != "skip")
    if actionable_count == 0:
        print("Nothing to send. Exiting.")
        return 0

    try:
        answer = input(f"Send these {actionable_count} emails and stamp the sheet? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in {"y", "yes"}:
        print("Aborted. No emails sent, no sheet writes.")
        return 1

    smtp = SMTPSettings(
        host=args.smtp_host,
        port=args.smtp_port,
        username=args.smtp_user,
        password=args.smtp_pass,
        from_address=args.from_address or args.smtp_user,
        use_tls=not args.smtp_ssl,
        use_ssl=args.smtp_ssl,
    )

    def send_fn(*, to_email, rendered):
        send_email(to_email=to_email, rendered=rendered, smtp=smtp)

    writer = SheetWriter(interest_ws)

    logger.info("Confirmed. Sending emails + stamping sheet...")
    run_once(
        interest_rows=interest_rows,
        application_rows=application_rows,
        writer=writer,
        send_fn=send_fn,
        max_reminders=args.max_reminders,
        now=datetime.now(),
    )
    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
