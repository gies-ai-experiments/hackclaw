"""Application reminder poller.

Periodically diffs the interest-form sheet against the application-form
sheet and emails @illinois.edu interest-form respondents who have not
yet submitted the application. Non-Gies respondents get a one-shot
"not eligible" email instead.

See ``docs/plans/2026-04-17-application-reminder-poller-design.md`` for
the design rationale.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from loguru import logger

from nanobot.onboard.email_canonical import canonical_illinois_email
from nanobot.onboard.parser import extract_members, is_gies_program


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
