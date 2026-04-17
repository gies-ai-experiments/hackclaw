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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from enum import Enum
from pathlib import Path
from typing import Protocol

from loguru import logger

from nanobot.onboard.email_canonical import canonical_illinois_email
from nanobot.onboard.parser import extract_members, is_gies_program
from nanobot.onboard.sheet_io import SheetWriter, stamp_not_eligible, stamp_reminder
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


def build_applied_set(rows: Iterable[list[str]]) -> set[str]:
    """Return canonical Illinois emails of every member on the application sheet.

    *rows* is an iterable of raw row lists (header already stripped).
    Non-``@illinois.edu`` emails are silently dropped.
    """
    applied: set[str] = set()
    for row in rows:
        for member in extract_members(row):
            canon = canonical_illinois_email(member.email)
            if canon is None:
                logger.warning(
                    "Application sheet member dropped (non-illinois email): {!r}",
                    member.email,
                )
                continue
            applied.add(canon)
    return applied
