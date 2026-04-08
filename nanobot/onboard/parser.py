"""Parse Google Forms CSV exports of team registration data.

Expected column layout (25 columns, 0-based):
    0: Team Name
    1: Number of Team Members
    2-5: Metadata (ignored)
    6-9: Member 1 — Full Name, UIUC Email Address, Program / Major, Academic Year
    10-13: Member 2
    14-17: Member 3
    18-21: Member 4
    22-24: Business Focus Area, Tool Comfort, Agreement (ignored)
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass

from loguru import logger

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    """A single team member with name and email."""

    name: str
    email: str


@dataclass
class Team:
    """A hackathon team consisting of a name and its members."""

    name: str
    members: list[Member]


# ---------------------------------------------------------------------------
# Column layout constants
# ---------------------------------------------------------------------------

# Each member occupies 4 consecutive columns: name, email, program, year.
_MEMBER_SLOTS = 4
_MEMBER_START_COL = 6  # Column index where member data begins.
_MAX_MEMBERS = 4


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_teams_csv(csv_path: str) -> list[Team]:
    """Read *csv_path* and return a list of :class:`Team` objects.

    Rows with an empty team name are silently skipped.  Within each row,
    member slots whose name **or** email is empty are also skipped.
    """
    teams: list[Team] = []

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # skip the header row

        for row_num, row in enumerate(reader, start=2):  # 2 because header was row 1
            team_name = row[0].strip() if row else ""
            if not team_name:
                logger.debug("Row {}: empty team name — skipped", row_num)
                continue

            members = _extract_members(row)
            teams.append(Team(name=team_name, members=members))
            logger.debug(
                "Row {}: team={!r} members={}",
                row_num,
                team_name,
                len(members),
            )

    logger.info("Parsed {} team(s) from {}", len(teams), csv_path)
    return teams


def slugify_team_name(name: str) -> str:
    """Convert a human-readable team name into a URL/channel-safe slug.

    ``"Alpha Agents"`` becomes ``"team-alpha-agents"``.

    Rules:
    1. Lowercase.
    2. Strip characters that are not alphanumeric, spaces, or hyphens.
    3. Collapse whitespace / hyphens into single hyphens.
    4. Strip leading / trailing hyphens.
    5. Prepend ``team-``.
    """
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)  # keep only alnum, spaces, hyphens
    slug = re.sub(r"[\s-]+", "-", slug)  # collapse whitespace/hyphens
    slug = slug.strip("-")
    return f"team-{slug}"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _extract_members(row: list[str]) -> list[Member]:
    """Pull up to 4 members from a CSV row, skipping empty slots."""
    members: list[Member] = []
    for i in range(_MAX_MEMBERS):
        base = _MEMBER_START_COL + i * _MEMBER_SLOTS
        if base + 1 >= len(row):
            break
        name = row[base].strip()
        email = row[base + 1].strip()
        if name and email:
            members.append(Member(name=name, email=email))
    return members
