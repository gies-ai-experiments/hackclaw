"""Tests for HelpTicket data model and TicketStore."""

from __future__ import annotations

from datetime import timezone

import pytest

from nanobot.helpqueue.ticket import HelpTicket, TicketStore


# ---------------------------------------------------------------------------
# HelpTicket dataclass
# ---------------------------------------------------------------------------


class TestHelpTicket:
    """Basic sanity checks on the HelpTicket dataclass."""

    def test_defaults(self) -> None:
        ticket = HelpTicket(
            id="HELP-001",
            team_name="Alpha",
            channel_id=123,
            location="Room 101",
            description="Need help with API",
        )
        assert ticket.status == "open"
        assert ticket.claimed_by is None
        assert ticket.claimed_by_name is None
        assert ticket.claimed_at is None
        assert ticket.resolved_at is None
        assert ticket.queue_message_id is None
        assert ticket.created_at.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# TicketStore.create
# ---------------------------------------------------------------------------


class TestTicketStoreCreate:
    """Creating tickets with auto-incrementing IDs."""

    def test_first_ticket_id(self) -> None:
        store = TicketStore()
        ticket = store.create(
            team_name="Alpha",
            channel_id=100,
            location="Room A",
            description="Help!",
        )
        assert ticket.id == "HELP-001"

    def test_auto_increment(self) -> None:
        store = TicketStore()
        t1 = store.create(team_name="A", channel_id=1, location="R1", description="d1")
        t2 = store.create(team_name="B", channel_id=2, location="R2", description="d2")
        t3 = store.create(team_name="C", channel_id=3, location="R3", description="d3")
        assert t1.id == "HELP-001"
        assert t2.id == "HELP-002"
        assert t3.id == "HELP-003"

    def test_created_ticket_is_open(self) -> None:
        store = TicketStore()
        ticket = store.create(
            team_name="Beta",
            channel_id=200,
            location="Room B",
            description="Stuck on deploy",
        )
        assert ticket.status == "open"

    def test_created_ticket_fields(self) -> None:
        store = TicketStore()
        ticket = store.create(
            team_name="Gamma",
            channel_id=300,
            location="Room C",
            description="Database issue",
        )
        assert ticket.team_name == "Gamma"
        assert ticket.channel_id == 300
        assert ticket.location == "Room C"
        assert ticket.description == "Database issue"

    def test_created_ticket_retrievable(self) -> None:
        store = TicketStore()
        ticket = store.create(
            team_name="Delta",
            channel_id=400,
            location="Room D",
            description="CSS broken",
        )
        retrieved = store.get(ticket.id)
        assert retrieved is ticket


# ---------------------------------------------------------------------------
# TicketStore.get
# ---------------------------------------------------------------------------


class TestTicketStoreGet:
    """Retrieving tickets by ID."""

    def test_get_existing(self) -> None:
        store = TicketStore()
        ticket = store.create(team_name="A", channel_id=1, location="R", description="d")
        assert store.get("HELP-001") is ticket

    def test_get_nonexistent(self) -> None:
        store = TicketStore()
        assert store.get("HELP-999") is None


# ---------------------------------------------------------------------------
# TicketStore.claim
# ---------------------------------------------------------------------------


class TestTicketStoreClaim:
    """Claiming open tickets."""

    def test_claim_open_ticket(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=1, location="R", description="d")
        result = store.claim("HELP-001", mentor_id="M1", mentor_name="Alice")
        assert result is True
        ticket = store.get("HELP-001")
        assert ticket is not None
        assert ticket.status == "claimed"
        assert ticket.claimed_by == "M1"
        assert ticket.claimed_by_name == "Alice"
        assert ticket.claimed_at is not None
        assert ticket.claimed_at.tzinfo == timezone.utc

    def test_claim_already_claimed_ticket(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=1, location="R", description="d")
        store.claim("HELP-001", mentor_id="M1", mentor_name="Alice")
        result = store.claim("HELP-001", mentor_id="M2", mentor_name="Bob")
        assert result is False
        # Original claimer unchanged
        ticket = store.get("HELP-001")
        assert ticket is not None
        assert ticket.claimed_by == "M1"

    def test_claim_resolved_ticket(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=1, location="R", description="d")
        store.resolve("HELP-001")
        result = store.claim("HELP-001", mentor_id="M1", mentor_name="Alice")
        assert result is False

    def test_claim_nonexistent_ticket(self) -> None:
        store = TicketStore()
        result = store.claim("HELP-999", mentor_id="M1", mentor_name="Alice")
        assert result is False


# ---------------------------------------------------------------------------
# TicketStore.unclaim
# ---------------------------------------------------------------------------


class TestTicketStoreUnclaim:
    """Unclaiming (releasing) a claimed ticket."""

    def test_unclaim_by_claimer(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=1, location="R", description="d")
        store.claim("HELP-001", mentor_id="M1", mentor_name="Alice")
        result = store.unclaim("HELP-001", mentor_id="M1")
        assert result is True
        ticket = store.get("HELP-001")
        assert ticket is not None
        assert ticket.status == "open"
        assert ticket.claimed_by is None
        assert ticket.claimed_by_name is None
        assert ticket.claimed_at is None

    def test_unclaim_wrong_mentor(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=1, location="R", description="d")
        store.claim("HELP-001", mentor_id="M1", mentor_name="Alice")
        result = store.unclaim("HELP-001", mentor_id="M2")
        assert result is False
        # Ticket stays claimed by original mentor
        ticket = store.get("HELP-001")
        assert ticket is not None
        assert ticket.status == "claimed"
        assert ticket.claimed_by == "M1"

    def test_unclaim_open_ticket(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=1, location="R", description="d")
        result = store.unclaim("HELP-001", mentor_id="M1")
        assert result is False

    def test_unclaim_resolved_ticket(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=1, location="R", description="d")
        store.resolve("HELP-001")
        result = store.unclaim("HELP-001", mentor_id="M1")
        assert result is False

    def test_unclaim_nonexistent_ticket(self) -> None:
        store = TicketStore()
        result = store.unclaim("HELP-999", mentor_id="M1")
        assert result is False


# ---------------------------------------------------------------------------
# TicketStore.resolve
# ---------------------------------------------------------------------------


class TestTicketStoreResolve:
    """Resolving tickets."""

    def test_resolve_open_ticket(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=1, location="R", description="d")
        result = store.resolve("HELP-001")
        assert result is True
        ticket = store.get("HELP-001")
        assert ticket is not None
        assert ticket.status == "resolved"
        assert ticket.resolved_at is not None
        assert ticket.resolved_at.tzinfo == timezone.utc

    def test_resolve_claimed_ticket(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=1, location="R", description="d")
        store.claim("HELP-001", mentor_id="M1", mentor_name="Alice")
        result = store.resolve("HELP-001")
        assert result is True
        ticket = store.get("HELP-001")
        assert ticket is not None
        assert ticket.status == "resolved"

    def test_resolve_already_resolved(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=1, location="R", description="d")
        store.resolve("HELP-001")
        result = store.resolve("HELP-001")
        assert result is False

    def test_resolve_nonexistent_ticket(self) -> None:
        store = TicketStore()
        result = store.resolve("HELP-999")
        assert result is False


# ---------------------------------------------------------------------------
# TicketStore.get_open_by_channel
# ---------------------------------------------------------------------------


class TestGetOpenByChannel:
    """Finding the active ticket for a given channel."""

    def test_find_open_ticket(self) -> None:
        store = TicketStore()
        ticket = store.create(team_name="A", channel_id=42, location="R", description="d")
        found = store.get_open_by_channel(42)
        assert found is ticket

    def test_find_claimed_ticket(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=42, location="R", description="d")
        store.claim("HELP-001", mentor_id="M1", mentor_name="Alice")
        found = store.get_open_by_channel(42)
        assert found is not None
        assert found.status == "claimed"

    def test_not_found_after_resolve(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=42, location="R", description="d")
        store.resolve("HELP-001")
        found = store.get_open_by_channel(42)
        assert found is None

    def test_not_found_wrong_channel(self) -> None:
        store = TicketStore()
        store.create(team_name="A", channel_id=42, location="R", description="d")
        found = store.get_open_by_channel(999)
        assert found is None

    def test_not_found_empty_store(self) -> None:
        store = TicketStore()
        found = store.get_open_by_channel(42)
        assert found is None


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    """End-to-end: create -> claim -> resolve."""

    def test_create_claim_resolve(self) -> None:
        store = TicketStore()
        ticket = store.create(
            team_name="Omega",
            channel_id=555,
            location="Lab 3",
            description="GPU not found",
        )
        assert ticket.status == "open"

        assert store.claim(ticket.id, mentor_id="M42", mentor_name="Eve") is True
        assert ticket.status == "claimed"
        assert store.get_open_by_channel(555) is ticket

        assert store.resolve(ticket.id) is True
        assert ticket.status == "resolved"
        assert store.get_open_by_channel(555) is None

    def test_create_claim_unclaim_reclaim_resolve(self) -> None:
        store = TicketStore()
        ticket = store.create(
            team_name="Sigma",
            channel_id=777,
            location="Atrium",
            description="WiFi down",
        )

        store.claim(ticket.id, mentor_id="M1", mentor_name="Alice")
        assert ticket.status == "claimed"

        store.unclaim(ticket.id, mentor_id="M1")
        assert ticket.status == "open"

        store.claim(ticket.id, mentor_id="M2", mentor_name="Bob")
        assert ticket.claimed_by == "M2"
        assert ticket.claimed_by_name == "Bob"

        store.resolve(ticket.id)
        assert ticket.status == "resolved"
        assert ticket.resolved_at is not None
