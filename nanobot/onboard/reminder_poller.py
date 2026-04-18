"""Application reminder poller.

Periodically diffs the interest-form sheet against the application-form
sheet and emails @illinois.edu interest-form respondents who have not
yet submitted the application. Non-Gies respondents get a one-shot
"not eligible" email instead.

See ``docs/plans/2026-04-17-application-reminder-poller-design.md`` for
the design rationale.
"""

from __future__ import annotations

import smtplib
import ssl
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from nanobot.config.loader import load_config
from nanobot.onboard.email_canonical import canonical_illinois_email
from nanobot.onboard.parser import extract_members, is_gies_program
from nanobot.onboard.reminder_config import ReminderPollerConfig
from nanobot.onboard.sheet_io import (
    SheetWriter,
    fetch_rows,
    open_client,
    open_first_worksheet,
    open_worksheet_by_gid,
    stamp_not_eligible,
    stamp_reminder,
)
from nanobot.onboard.templates import RenderedEmail, load_template, render


class Action(str, Enum):
    """What the poller should do with a given interest-sheet row."""

    SKIP = "skip"
    REMINDER = "reminder"
    NOT_ELIGIBLE = "not_eligible"


@dataclass(frozen=True)
class InterestRow:
    """A single interest-sheet row, normalized for decision-making.

    ``row_index`` is the 1-based data row number (header excluded), used
    for column writebacks. ``raw_email`` is the user-entered email;
    classification re-canonicalizes so the same logic applies whether the
    value came in clean or typo'd.
    """

    row_index: int
    name: str
    raw_email: str
    program: str
    reminder_count: int
    not_eligible_sent_at: str  # ISO timestamp or ""


def classify(row: InterestRow, *, applied: set[str], max_reminders: int) -> Action:
    """Decide what to do with *row*.

    Order of checks (each short-circuits):
    1. Email cannot be canonicalized to ``@illinois.edu`` -> SKIP.
    2. Email is in *applied* (they submitted) -> SKIP.
    3. Program is Gies-eligible:
       - reminder_count < max_reminders -> REMINDER
       - else -> SKIP (cap reached)
    4. Program is not Gies-eligible:
       - not_eligible_sent_at empty -> NOT_ELIGIBLE
       - else -> SKIP (already told them once)
    """
    canon = canonical_illinois_email(row.raw_email)
    if canon is None:
        return Action.SKIP
    if canon in applied:
        return Action.SKIP
    if is_gies_program(row.program):
        if row.reminder_count < max_reminders:
            return Action.REMINDER
        return Action.SKIP
    if row.not_eligible_sent_at.strip():
        return Action.SKIP
    return Action.NOT_ELIGIBLE


INTEREST_COL_NAME = 1
INTEREST_COL_EMAIL = 2
INTEREST_COL_PROGRAM = 5
INTEREST_COL_REMINDER_COUNT = 11    # column L
INTEREST_COL_LAST_REMINDER_AT = 12  # column M
INTEREST_COL_NOT_ELIGIBLE_AT = 13   # column N


def _cell(row: list[str], idx: int) -> str:
    """Return ``row[idx]`` stripped, or '' if the row is shorter than *idx+1*."""
    if idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def parse_interest_row(row_index: int, raw: list[str]) -> InterestRow:
    """Build an :class:`InterestRow` from a 1-based data row index.

    Missing dedup columns default to ``0`` / ``""``. A non-integer
    ``Reminder Count`` cell is logged and treated as ``0`` so a one-off
    typo can't permanently lock a row.
    """
    count_raw = _cell(raw, INTEREST_COL_REMINDER_COUNT)
    try:
        reminder_count = int(count_raw) if count_raw else 0
    except ValueError:
        logger.warning(
            "Row {}: invalid Reminder Count {!r}; treating as 0", row_index, count_raw
        )
        reminder_count = 0
    return InterestRow(
        row_index=row_index,
        name=_cell(raw, INTEREST_COL_NAME),
        raw_email=_cell(raw, INTEREST_COL_EMAIL),
        program=_cell(raw, INTEREST_COL_PROGRAM),
        reminder_count=reminder_count,
        not_eligible_sent_at=_cell(raw, INTEREST_COL_NOT_ELIGIBLE_AT),
    )


@dataclass(frozen=True)
class SMTPSettings:
    """SMTP credentials. Sourced from ``config.json -> channels.email``."""

    host: str
    port: int
    username: str
    password: str
    from_address: str
    use_tls: bool = True
    use_ssl: bool = False


def send_email(*, to_email: str, rendered: RenderedEmail, smtp: SMTPSettings) -> None:
    """Send a rendered email via SMTP. Raises on transport failure."""
    msg = EmailMessage()
    msg["From"] = smtp.from_address or smtp.username
    msg["To"] = to_email
    msg["Subject"] = rendered.subject
    msg.set_content(rendered.body)

    timeout = 30
    if smtp.use_ssl:
        with smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=timeout) as conn:
            conn.login(smtp.username, smtp.password)
            conn.send_message(msg)
        return
    with smtplib.SMTP(smtp.host, smtp.port, timeout=timeout) as conn:
        if smtp.use_tls:
            conn.starttls(context=ssl.create_default_context())
        conn.login(smtp.username, smtp.password)
        conn.send_message(msg)


_TEMPLATE_DIR = Path(__file__).parent
_REMINDER_TEMPLATE_PATH = _TEMPLATE_DIR / "reminder_email_template.txt"
_NOT_ELIGIBLE_TEMPLATE_PATH = _TEMPLATE_DIR / "not_eligible_email_template.txt"


class SendFn(Protocol):
    """Protocol for the inject-able SMTP sender."""

    def __call__(self, *, to_email: str, rendered: RenderedEmail) -> None: ...


def _render_for(action: Action, *, name: str) -> RenderedEmail:
    if action is Action.REMINDER:
        raw = load_template(_REMINDER_TEMPLATE_PATH)
    else:
        raw = load_template(_NOT_ELIGIBLE_TEMPLATE_PATH)
    return render(raw, name=name)


@dataclass(frozen=True)
class Decision:
    """The outcome of a single interest-row classification, for preview."""

    row_index: int
    name: str
    canonical_email: str  # empty if not canonicalizable
    program: str
    action: Action
    reminder_count: int  # current value before any increment


def preview_decisions(
    *,
    interest_rows: list[list[str]],
    application_rows: list[list[str]],
    max_reminders: int,
) -> list[Decision]:
    """Classify every interest row without sending or stamping.

    Returned list includes **all** rows (including ``SKIP``) so the
    caller can print a full audit of what would happen.
    """
    applied = build_applied_set(application_rows)
    decisions: list[Decision] = []
    for idx, raw in enumerate(interest_rows, start=1):
        ir = parse_interest_row(idx, raw)
        action = classify(ir, applied=applied, max_reminders=max_reminders)
        canon = canonical_illinois_email(ir.raw_email) or ""
        decisions.append(
            Decision(
                row_index=idx,
                name=ir.name,
                canonical_email=canon,
                program=ir.program,
                action=action,
                reminder_count=ir.reminder_count,
            )
        )
    return decisions


def format_preview(decisions: list[Decision]) -> str:
    """Human-readable table of pending decisions, grouped by action."""
    actionable = [d for d in decisions if d.action is not Action.SKIP]
    skipped = [d for d in decisions if d.action is Action.SKIP]

    lines: list[str] = []
    lines.append(f"Total interest rows: {len(decisions)}")
    lines.append(
        f"  Reminders to send:     {sum(1 for d in actionable if d.action is Action.REMINDER)}"
    )
    lines.append(
        f"  Not-eligible to send:  {sum(1 for d in actionable if d.action is Action.NOT_ELIGIBLE)}"
    )
    lines.append(f"  Skipped:               {len(skipped)}")
    lines.append("")

    if actionable:
        lines.append("Will send:")
        lines.append(f"  {'ROW':>3}  {'ACTION':<13}  {'NAME':<28}  {'EMAIL':<32}  PROGRAM")
        lines.append("  " + "-" * 100)
        for d in actionable:
            action_str = d.action.value + (
                f" (#{d.reminder_count + 1}/{d.reminder_count + 1})"
                if d.action is Action.REMINDER
                else ""
            )
            lines.append(
                f"  {d.row_index:>3}  {d.action.value:<13}  "
                f"{(d.name or '<blank>')[:28]:<28}  "
                f"{(d.canonical_email or '<none>')[:32]:<32}  "
                f"{d.program[:30]}"
            )
    else:
        lines.append("No actionable rows — nothing to send.")

    return "\n".join(lines)


def run_once(
    *,
    interest_rows: list[list[str]],
    application_rows: list[list[str]],
    writer: SheetWriter,
    send_fn: SendFn,
    max_reminders: int,
    now: datetime,
) -> None:
    """One full poll cycle. Pure orchestration; all I/O is injected.

    On send failure for a single row: log and continue, **do not** stamp
    the dedup columns, so the next cycle naturally retries.
    """
    applied = build_applied_set(application_rows)
    logger.info(
        "Reminder poller cycle: {} applicants, {} interest rows",
        len(applied),
        len(interest_rows),
    )

    for idx, raw in enumerate(interest_rows, start=1):
        ir = parse_interest_row(idx, raw)
        action = classify(ir, applied=applied, max_reminders=max_reminders)
        if action is Action.SKIP:
            continue

        canon = canonical_illinois_email(ir.raw_email) or ir.raw_email
        rendered = _render_for(action, name=ir.name or "there")

        try:
            send_fn(to_email=canon, rendered=rendered)
        except Exception as exc:
            logger.error(
                "Failed to send {} to {} (row {}): {}; will retry next cycle",
                action.value,
                canon,
                idx,
                exc,
            )
            continue

        if action is Action.REMINDER:
            stamp_reminder(
                writer, row_index=idx, new_count=ir.reminder_count + 1, now=now,
            )
        else:
            stamp_not_eligible(writer, row_index=idx, now=now)


def smtp_settings_from_config(config: Any) -> SMTPSettings:
    """Pull SMTP creds out of the nanobot ``Config`` object."""
    email_cfg = config.channels.email
    return SMTPSettings(
        host=email_cfg.smtp_host,
        port=email_cfg.smtp_port,
        username=email_cfg.smtp_username,
        password=email_cfg.smtp_password,
        from_address=email_cfg.from_address or email_cfg.smtp_username,
        use_tls=email_cfg.smtp_use_tls,
        use_ssl=email_cfg.smtp_use_ssl,
    )


def _build_send_fn(smtp: SMTPSettings) -> SendFn:
    def send(*, to_email: str, rendered: RenderedEmail) -> None:
        send_email(to_email=to_email, rendered=rendered, smtp=smtp)
    return send


def main() -> None:
    """Run the reminder poller loop forever."""
    cfg = ReminderPollerConfig.from_env()
    nb_config = load_config()
    smtp = smtp_settings_from_config(nb_config)

    client = open_client(cfg.service_account_path)
    interest_ws = open_worksheet_by_gid(client, cfg.interest_sheet_id, cfg.interest_sheet_gid)
    application_ws = open_first_worksheet(client, cfg.application_sheet_id)
    writer = SheetWriter(interest_ws)
    send_fn = _build_send_fn(smtp)

    logger.info(
        "Reminder poller starting: interest={} app={} interval={}s cap={}",
        cfg.interest_sheet_id,
        cfg.application_sheet_id,
        cfg.poll_interval_seconds,
        cfg.max_reminders,
    )

    while True:
        try:
            interest_rows = fetch_rows(interest_ws)
            application_rows = fetch_rows(application_ws)
            run_once(
                interest_rows=interest_rows,
                application_rows=application_rows,
                writer=writer,
                send_fn=send_fn,
                max_reminders=cfg.max_reminders,
                now=datetime.now(),
            )
        except Exception:
            logger.exception("Reminder poller cycle crashed; sleeping and retrying")
        time.sleep(cfg.poll_interval_seconds)


if __name__ == "__main__":
    main()


def build_applied_set(rows: Iterable[list[str]]) -> set[str]:
    """Return canonical Illinois emails of every member on the application sheet.

    *rows* is an iterable of raw row lists (header already stripped).
    Non-``@illinois.edu`` emails are silently dropped.

    The live Google Sheet appends a ``Timestamp`` column at position 0 that
    is absent from the CSV export ``parser.py`` was originally written for.
    We strip it before delegating to :func:`extract_members`, which assumes
    team-name at col 0 and member 1 at col 6.
    """
    applied: set[str] = set()
    for row in rows:
        trimmed = row[1:] if row else row  # drop Timestamp column
        for member in extract_members(trimmed):
            canon = canonical_illinois_email(member.email)
            if canon is None:
                logger.warning(
                    "Application sheet member dropped (non-illinois email): {!r}",
                    member.email,
                )
                continue
            applied.add(canon)
    return applied
