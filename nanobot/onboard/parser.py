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

Eligibility:
    The Gies AI for Impact Challenge is open only to Gies College of Business
    students.  :func:`parse_teams_csv` validates each member's declared program
    against :data:`GIES_PROGRAM_KEYWORDS` and, by default, filters out anyone
    who does not look like a Gies student.  Teams that end up with zero eligible
    members are dropped entirely.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field

from loguru import logger

# ---------------------------------------------------------------------------
# Gies College of Business program whitelist
# ---------------------------------------------------------------------------

# Canonical list of program / major keywords that indicate a student is
# enrolled in the Gies College of Business at UIUC.  Matching is case
# insensitive and uses word boundaries, so short abbreviations (e.g. "IS",
# "MBA") only match when they appear as whole words.  Extend this list when
# new Gies degree programs appear on the registration form.
GIES_PROGRAM_KEYWORDS: frozenset[str] = frozenset(
    {
        # Undergraduate majors
        "accountancy",
        "accounting",
        "accy",
        "finance",
        "fin",
        "information systems",
        "mis",
        "is",
        "marketing",
        "management",
        "mgmt",
        "operations management",
        "operations",
        "supply chain management",
        "supply chain",
        "scm",
        "strategy",
        "entrepreneurship",
        "business administration",
        "business analytics",
        "badm",
        "business",
        # Graduate degrees (in-person only — online variants are excluded below)
        "mba",
        "msa",
        "msf",
        "msba",
        "mstm",           # MS Technology Management
        "msm",            # MS Management
        "master of accountancy",
        "master of finance",
        "master of business",
        "technology management",
        "information system",  # catches singular typos
        # Catch-all / college name
        "gies",
    }
)

# Pre-compiled whole-word matchers for every keyword above.  Using
# ``\b`` keeps abbreviations like "IS" from matching inside words such as
# "Visual" or "Physics".
_GIES_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE) for kw in GIES_PROGRAM_KEYWORDS
)

# Online-program exclusions. The hackathon is in-person only, so online
# Gies programs (iMBA, iMSM, iMSA) are filtered out even though their
# non-online counterparts (MBA, MSM, MSA) are valid. Anything containing
# the word "online" is also excluded.
_ONLINE_EXCLUSION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"\b{kw}\b", re.IGNORECASE)
    for kw in ("online", "imba", "imsm", "imsa")
)


def is_gies_program(program: str) -> bool:
    """Return ``True`` when *program* looks like an **in-person** Gies program.

    Matches any whole-word Gies keyword (see :data:`GIES_PROGRAM_KEYWORDS`)
    anywhere inside the field. Empty or whitespace-only strings return
    ``False``. Online variants (``online`` anywhere in the text, or the
    ``i``-prefixed variants ``iMBA``/``iMSM``/``iMSA``) are explicitly
    excluded because the event is on-campus only.
    """
    if not program or not program.strip():
        return False
    if any(p.search(program) for p in _ONLINE_EXCLUSION_PATTERNS):
        return False
    return any(pattern.search(program) for pattern in _GIES_PATTERNS)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    """A single team member parsed from the registration form."""

    name: str
    email: str
    program: str = ""
    academic_year: str = ""

    @property
    def is_gies(self) -> bool:
        """``True`` when this member's program looks like a Gies program."""
        return is_gies_program(self.program)


@dataclass
class Team:
    """A hackathon team consisting of a name and its members."""

    name: str
    members: list[Member] = field(default_factory=list)

    def gies_members(self) -> list[Member]:
        """Return the subset of members enrolled in Gies."""
        return [m for m in self.members if m.is_gies]

    def non_gies_members(self) -> list[Member]:
        """Return the subset of members who are **not** Gies students."""
        return [m for m in self.members if not m.is_gies]

    def is_all_gies(self) -> bool:
        """``True`` when the team has members and every one is Gies-eligible."""
        return bool(self.members) and all(m.is_gies for m in self.members)


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


def parse_teams_csv(csv_path: str, *, gies_only: bool = True) -> list[Team]:
    """Read *csv_path* and return a list of :class:`Team` objects.

    Rows with an empty team name are silently skipped.  Within each row,
    member slots whose name **or** email is empty are also skipped.

    Args:
        csv_path: Path to the Google Forms CSV export.
        gies_only: When ``True`` (the default), members whose declared program
            does not match any Gies keyword are logged as a warning and
            filtered out.  Teams that end up with zero eligible members after
            filtering are dropped entirely.  When ``False``, every member is
            retained and no eligibility filtering is performed — useful for
            diagnostics or downstream custom validation.
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

            members = extract_members(row)

            if gies_only:
                eligible: list[Member] = []
                for member in members:
                    if member.is_gies:
                        eligible.append(member)
                    else:
                        logger.warning(
                            "Row {}: team={!r} member={!r} program={!r} is not a "
                            "Gies College of Business program — excluded",
                            row_num,
                            team_name,
                            member.name,
                            member.program,
                        )
                if not eligible:
                    logger.warning(
                        "Row {}: team={!r} has no Gies-eligible members — team skipped",
                        row_num,
                        team_name,
                    )
                    continue
                members = eligible

            teams.append(Team(name=team_name, members=members))
            logger.debug(
                "Row {}: team={!r} members={}",
                row_num,
                team_name,
                len(members),
            )

    logger.info(
        "Parsed {} team(s) from {} (gies_only={})",
        len(teams),
        csv_path,
        gies_only,
    )
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


def extract_members(row: list[str]) -> list[Member]:
    """Pull up to 4 members from a CSV row, skipping empty slots."""
    members: list[Member] = []
    for i in range(_MAX_MEMBERS):
        base = _MEMBER_START_COL + i * _MEMBER_SLOTS
        # Need all 4 member columns (name, email, program, year) to be addressable.
        if base + 3 >= len(row):
            break
        name = row[base].strip()
        email = row[base + 1].strip()
        program = row[base + 2].strip()
        academic_year = row[base + 3].strip()
        if name and email:
            members.append(
                Member(
                    name=name,
                    email=email,
                    program=program,
                    academic_year=academic_year,
                )
            )
    return members
