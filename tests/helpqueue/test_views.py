"""Tests for Discord embed builder and button views."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

discord = pytest.importorskip("discord")

from nanobot.helpqueue.ticket import HelpTicket
from nanobot.helpqueue.views import (
    ClaimView,
    STATUS_COLOURS,
    build_ticket_embed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ticket(**overrides) -> HelpTicket:
    """Return a HelpTicket with sensible defaults, overridable via kwargs."""
    defaults = dict(
        id="HELP-001",
        team_name="Alpha",
        channel_id=123,
        location="Room 101",
        description="Need help with API",
        status="open",
        created_at=datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return HelpTicket(**defaults)


# ---------------------------------------------------------------------------
# Open ticket embed
# ---------------------------------------------------------------------------


class TestOpenTicketEmbed:
    """Embed for an open ticket should have orange colour and core fields."""

    def test_colour_is_orange(self) -> None:
        ticket = _make_ticket(status="open")
        embed = build_ticket_embed(ticket)
        assert embed.colour == discord.Colour(0xFF9900)

    def test_title_format(self) -> None:
        ticket = _make_ticket(id="HELP-042", team_name="BetaTeam", status="open")
        embed = build_ticket_embed(ticket)
        # Title now includes a mode badge (default in-person) suffix.
        assert embed.title.startswith("HELP-042 \u2014 BetaTeam")
        assert "IN-PERSON" in embed.title

    def test_location_field(self) -> None:
        ticket = _make_ticket(location="Lab 3")
        embed = build_ticket_embed(ticket)
        location_field = next(f for f in embed.fields if f.name == "Location")
        assert location_field.value == "Lab 3"
        assert location_field.inline is True

    def test_description_field(self) -> None:
        ticket = _make_ticket(description="Docker won't start")
        embed = build_ticket_embed(ticket)
        desc_field = next(f for f in embed.fields if f.name == "Description")
        assert desc_field.value == "Docker won't start"
        assert desc_field.inline is False

    def test_status_field_shows_waiting(self) -> None:
        ticket = _make_ticket(status="open")
        embed = build_ticket_embed(ticket)
        status_field = next(f for f in embed.fields if f.name == "Status")
        assert status_field.value == "Waiting for mentor"

    def test_no_claimed_by_field(self) -> None:
        ticket = _make_ticket(status="open")
        embed = build_ticket_embed(ticket)
        field_names = [f.name for f in embed.fields]
        assert "Claimed by" not in field_names

    def test_timestamp_set(self) -> None:
        created = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
        ticket = _make_ticket(created_at=created)
        embed = build_ticket_embed(ticket)
        assert embed.timestamp == created


# ---------------------------------------------------------------------------
# Claimed ticket embed
# ---------------------------------------------------------------------------


class TestClaimedTicketEmbed:
    """Embed for a claimed ticket should be blue and show the mentor."""

    def test_colour_is_blue(self) -> None:
        ticket = _make_ticket(
            status="claimed",
            claimed_by="M1",
            claimed_by_name="Alice",
            claimed_at=datetime(2026, 4, 14, 12, 5, 0, tzinfo=timezone.utc),
        )
        embed = build_ticket_embed(ticket)
        assert embed.colour == discord.Colour(0x3498DB)

    def test_claimed_by_field(self) -> None:
        ticket = _make_ticket(
            status="claimed",
            claimed_by="M1",
            claimed_by_name="Alice",
            claimed_at=datetime(2026, 4, 14, 12, 5, 0, tzinfo=timezone.utc),
        )
        embed = build_ticket_embed(ticket)
        claimed_field = next(f for f in embed.fields if f.name == "Claimed by")
        assert claimed_field.value == "Alice"

    def test_status_field_shows_mentor_on_the_way(self) -> None:
        ticket = _make_ticket(
            status="claimed",
            claimed_by="M1",
            claimed_by_name="Alice",
        )
        embed = build_ticket_embed(ticket)
        status_field = next(f for f in embed.fields if f.name == "Status")
        assert status_field.value == "Mentor on the way"


# ---------------------------------------------------------------------------
# Resolved ticket embed
# ---------------------------------------------------------------------------


class TestResolvedTicketEmbed:
    """Embed for a resolved ticket should be green and show resolution time."""

    def test_colour_is_green(self) -> None:
        ticket = _make_ticket(
            status="resolved",
            created_at=datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc),
            resolved_at=datetime(2026, 4, 14, 12, 15, 0, tzinfo=timezone.utc),
        )
        embed = build_ticket_embed(ticket)
        assert embed.colour == discord.Colour(0x2ECC71)

    def test_resolved_in_field(self) -> None:
        created = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
        resolved = created + timedelta(minutes=23)
        ticket = _make_ticket(
            status="resolved",
            created_at=created,
            resolved_at=resolved,
        )
        embed = build_ticket_embed(ticket)
        resolved_field = next(f for f in embed.fields if f.name == "Resolved in")
        assert resolved_field.value == "23 min"

    def test_status_field_shows_resolved(self) -> None:
        ticket = _make_ticket(
            status="resolved",
            created_at=datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc),
            resolved_at=datetime(2026, 4, 14, 12, 10, 0, tzinfo=timezone.utc),
        )
        embed = build_ticket_embed(ticket)
        status_field = next(f for f in embed.fields if f.name == "Status")
        assert status_field.value == "Resolved"


# ---------------------------------------------------------------------------
# ClaimView
# ---------------------------------------------------------------------------


class TestClaimView:
    """Basic structural checks on the ClaimView."""

    async def test_stores_ticket_id(self) -> None:
        view = ClaimView("HELP-007")
        assert view.ticket_id == "HELP-007"

    async def test_timeout_is_none(self) -> None:
        view = ClaimView("HELP-001")
        assert view.timeout is None
