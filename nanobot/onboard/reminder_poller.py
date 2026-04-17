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

from loguru import logger

from nanobot.onboard.email_canonical import canonical_illinois_email
from nanobot.onboard.parser import extract_members


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
