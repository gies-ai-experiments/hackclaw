"""Tests for the CSV parser that reads Google Forms team registration exports."""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.onboard.parser import Member, parse_teams_csv, slugify_team_name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 25 columns total:
#  0: Team Name
#  1: Number of Team Members
#  2-5: Metadata (ignored)
#  6-9: Member 1 (Name, Email, Program, Year)
# 10-13: Member 2
# 14-17: Member 3
# 18-21: Member 4
# 22-24: More metadata (ignored)

HEADER = (
    "Team Name,Number of Team Members,Meta1,Meta2,Meta3,Meta4,"
    "Full Name,UIUC Email Address,Program / Major,Academic Year,"
    "Full Name,UIUC Email Address,Program / Major,Academic Year,"
    "Full Name,UIUC Email Address,Program / Major,Academic Year,"
    "Full Name,UIUC Email Address,Program / Major,Academic Year,"
    "Business Focus Area,Tool Comfort,Agreement"
)


def _write_csv(tmp_path: Path, rows: list[str]) -> Path:
    """Write a CSV with the standard header and the given data rows."""
    csv_path = tmp_path / "teams.csv"
    content = HEADER + "\n" + "\n".join(rows) + "\n"
    csv_path.write_text(content)
    return csv_path


def _make_row(
    team_name: str,
    member_count: str,
    members: list[tuple[str, str, str, str]],
) -> str:
    """Build a CSV row with the given team name, member count, and up to 4 members.

    Each member is (name, email, program, year). Pad to 4 member slots with empties.
    """
    # Columns 0-1
    parts: list[str] = [team_name, member_count]
    # Columns 2-5: metadata (ignored)
    parts.extend(["meta", "meta", "meta", "meta"])
    # Columns 6-21: 4 member slots (4 fields each)
    for i in range(4):
        if i < len(members):
            parts.extend(members[i])
        else:
            parts.extend(["", "", "", ""])
    # Columns 22-24: trailing metadata (ignored)
    parts.extend(["focus", "comfort", "yes"])
    return ",".join(parts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_parse_single_team_two_members(tmp_path: Path) -> None:
    """Parse a single row with exactly 2 members."""
    row = _make_row(
        "Alpha Agents",
        "2",
        [
            ("Alice Smith", "alice@illinois.edu", "Finance", "Junior"),
            ("Bob Jones", "bob@illinois.edu", "CS", "Senior"),
        ],
    )
    csv_path = _write_csv(tmp_path, [row])
    teams = parse_teams_csv(str(csv_path))

    assert len(teams) == 1
    team = teams[0]
    assert team.name == "Alpha Agents"
    assert len(team.members) == 2
    assert team.members[0] == Member(name="Alice Smith", email="alice@illinois.edu")
    assert team.members[1] == Member(name="Bob Jones", email="bob@illinois.edu")


def test_parse_skips_empty_members(tmp_path: Path) -> None:
    """Team has 1 real member; remaining slots are empty and should be ignored."""
    row = _make_row(
        "Solo Squad",
        "1",
        [
            ("Carol Wu", "carol@illinois.edu", "Accounting", "Sophomore"),
        ],
    )
    csv_path = _write_csv(tmp_path, [row])
    teams = parse_teams_csv(str(csv_path))

    assert len(teams) == 1
    assert len(teams[0].members) == 1
    assert teams[0].members[0] == Member(name="Carol Wu", email="carol@illinois.edu")


def test_parse_multiple_teams(tmp_path: Path) -> None:
    """Two rows should produce two Team objects."""
    row1 = _make_row(
        "Team One",
        "2",
        [
            ("Dan Lee", "dan@illinois.edu", "IS", "Senior"),
            ("Eve Park", "eve@illinois.edu", "Marketing", "Junior"),
        ],
    )
    row2 = _make_row(
        "Team Two",
        "3",
        [
            ("Fay Kim", "fay@illinois.edu", "Finance", "Freshman"),
            ("Gil Rao", "gil@illinois.edu", "CS", "Sophomore"),
            ("Hao Lin", "hao@illinois.edu", "Econ", "Junior"),
        ],
    )
    csv_path = _write_csv(tmp_path, [row1, row2])
    teams = parse_teams_csv(str(csv_path))

    assert len(teams) == 2
    assert teams[0].name == "Team One"
    assert len(teams[0].members) == 2
    assert teams[1].name == "Team Two"
    assert len(teams[1].members) == 3


def test_parse_rejects_missing_team_name(tmp_path: Path) -> None:
    """A row with an empty team name should be silently skipped."""
    valid_row = _make_row(
        "Valid Team",
        "1",
        [("Ivy Xu", "ivy@illinois.edu", "BADM", "Senior")],
    )
    empty_row = _make_row(
        "",
        "1",
        [("Nobody", "nobody@illinois.edu", "CS", "Junior")],
    )
    csv_path = _write_csv(tmp_path, [valid_row, empty_row])
    teams = parse_teams_csv(str(csv_path))

    assert len(teams) == 1
    assert teams[0].name == "Valid Team"


@pytest.mark.parametrize(
    "name, expected_slug",
    [
        ("Alpha Agents", "team-alpha-agents"),
        ("  Beta   Squad  ", "team-beta-squad"),
        ("Team With CAPS", "team-team-with-caps"),
        ("special!@#chars$%^", "team-specialchars"),
        ("already-hyphenated", "team-already-hyphenated"),
        ("  lots   of   spaces  ", "team-lots-of-spaces"),
    ],
)
def test_slugify_team_name(name: str, expected_slug: str) -> None:
    """Slugify converts names to lowercase, strips special chars, and hyphenates."""
    assert slugify_team_name(name) == expected_slug


def test_extract_members_public_helper(tmp_path: Path) -> None:
    """extract_members is exported and pulls members from a row list."""
    from nanobot.onboard.parser import extract_members

    row = ["Team", "1", "m", "m", "m", "m"]
    row += ["Alice", "alice@illinois.edu", "Finance", "Junior"]
    row += ["", "", "", ""] * 3
    row += ["focus", "comfort", "yes"]
    members = extract_members(row)
    assert len(members) == 1
    assert members[0].name == "Alice"
    assert members[0].email == "alice@illinois.edu"
