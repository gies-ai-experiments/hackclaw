"""Canonicalize and validate @illinois.edu email addresses.

Used by the application-reminder poller to make matching across the
interest sheet and the application sheet robust to capitalization and to
common one- or two-character typos in the ``illinois.edu`` domain.
"""

from __future__ import annotations

from loguru import logger

ILLINOIS_DOMAIN = "illinois.edu"
_FUZZY_MAX_DISTANCE = 2


def levenshtein(a: str, b: str) -> int:
    """Return the Levenshtein edit distance between *a* and *b*."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def canonical_illinois_email(email: str) -> str | None:
    """Return ``local@illinois.edu`` if *email* parses as a UIUC address.

    - Trims whitespace, lowercases.
    - Requires a non-empty local part and an ``@``.
    - Accepts ``illinois.edu`` exactly, or a domain within Levenshtein
      distance ``2`` of it (logged as a warning).
    - Returns ``None`` for everything else (no ``@``, missing local
      part, unrelated domain).
    """
    if not email:
        return None
    e = email.strip().lower()
    if "@" not in e:
        return None
    local, _, domain = e.partition("@")
    if not local or not domain:
        return None
    if domain == ILLINOIS_DOMAIN:
        return e
    if levenshtein(domain, ILLINOIS_DOMAIN) <= _FUZZY_MAX_DISTANCE:
        logger.warning("Fuzzy-corrected email domain: {!r} -> {}@{}", e, local, ILLINOIS_DOMAIN)
        return f"{local}@{ILLINOIS_DOMAIN}"
    return None
