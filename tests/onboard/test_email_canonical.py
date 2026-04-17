"""Tests for the @illinois.edu email canonicalizer."""
from __future__ import annotations

import pytest

from nanobot.onboard.email_canonical import canonical_illinois_email, levenshtein


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("", "", 0),
        ("kitten", "sitting", 3),
        ("illinois.edu", "illinois.edu", 0),
        ("illinois.edu", "ilinois.edu", 1),
        ("illinois.edu", "illionis.edu", 2),
        ("illinois.edu", "gmail.com", 10),
    ],
)
def test_levenshtein(a: str, b: str, expected: int) -> None:
    assert levenshtein(a, b) == expected


def test_canonical_exact_match() -> None:
    assert canonical_illinois_email("Alice@Illinois.EDU ") == "alice@illinois.edu"


@pytest.mark.parametrize(
    "raw",
    [
        "ash@ilinois.edu",     # 1 missing
        "ash@illinoi.edu",     # 1 missing
        "ash@illinois.ed",     # 1 missing
        "ash@illnois.edu",     # 1 missing
        "ash@illionis.edu",    # transposition (2 edits)
    ],
)
def test_canonical_fuzzy_corrects_typos(raw: str) -> None:
    assert canonical_illinois_email(raw) == "ash@illinois.edu"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no-at-sign",
        "ash@gmail.com",
        "ash@uiuc.edu",
        "ash@",
        "@illinois.edu",
    ],
)
def test_canonical_rejects_non_illinois(raw: str) -> None:
    assert canonical_illinois_email(raw) is None
