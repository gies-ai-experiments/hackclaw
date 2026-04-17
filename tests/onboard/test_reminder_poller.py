"""Tests for the application reminder poller."""
from __future__ import annotations

from nanobot.onboard.reminder_poller import build_applied_set


def _app_row(*members: tuple[str, str, str, str]) -> list[str]:
    """Build a 25-col application row with up to 4 members."""
    row = ["TeamX", str(len(members)), "m", "m", "m", "m"]
    for i in range(4):
        if i < len(members):
            row.extend(members[i])
        else:
            row.extend(["", "", "", ""])
    row.extend(["focus", "comfort", "yes"])
    return row


def test_build_applied_set_one_team() -> None:
    rows = [
        _app_row(
            ("Alice", "ALICE@illinois.edu", "Finance", "Junior"),
            ("Bob", "bob@ilinois.edu", "Accounting", "Senior"),  # typo'd domain
        ),
    ]
    applied = build_applied_set(rows)
    assert applied == {"alice@illinois.edu", "bob@illinois.edu"}


def test_build_applied_set_drops_non_illinois() -> None:
    rows = [_app_row(("Carol", "carol@gmail.com", "Marketing", "Senior"))]
    assert build_applied_set(rows) == set()


def test_build_applied_set_skips_blank_rows() -> None:
    rows = [["", "", "", "", "", ""]]  # too short, no members
    assert build_applied_set(rows) == set()
