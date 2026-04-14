"""HelpTicket data model and in-memory TicketStore for the help-queue system."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class HelpTicket:
    """A single help ticket submitted by a hackathon team."""

    id: str                             # e.g. "HELP-001"
    team_name: str
    channel_id: int
    location: str
    description: str
    status: str = "open"                # open | claimed | resolved
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    claimed_by: str | None = None       # mentor Discord ID
    claimed_by_name: str | None = None  # mentor display name
    claimed_at: datetime | None = None
    resolved_at: datetime | None = None
    queue_message_id: int | None = None


class TicketStore:
    """Thread-safe, in-memory store for :class:`HelpTicket` instances.

    Tickets are auto-assigned sequential IDs of the form ``HELP-001``,
    ``HELP-002``, etc.
    """

    def __init__(self) -> None:
        self._tickets: dict[str, HelpTicket] = {}
        self._counter: int = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        team_name: str,
        channel_id: int,
        location: str,
        description: str,
    ) -> HelpTicket:
        """Create a new ticket and return it."""
        with self._lock:
            self._counter += 1
            ticket_id = f"HELP-{self._counter:03d}"
            ticket = HelpTicket(
                id=ticket_id,
                team_name=team_name,
                channel_id=channel_id,
                location=location,
                description=description,
            )
            self._tickets[ticket_id] = ticket
        return ticket

    def get(self, ticket_id: str) -> HelpTicket | None:
        """Return a ticket by its ID, or ``None`` if not found."""
        return self._tickets.get(ticket_id)

    def claim(self, ticket_id: str, *, mentor_id: str, mentor_name: str) -> bool:
        """Claim an open ticket on behalf of a mentor.

        Returns ``True`` on success, ``False`` if the ticket does not
        exist or is not in the ``"open"`` state.
        """
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None or ticket.status != "open":
                return False
            ticket.status = "claimed"
            ticket.claimed_by = mentor_id
            ticket.claimed_by_name = mentor_name
            ticket.claimed_at = datetime.now(timezone.utc)
        return True

    def unclaim(self, ticket_id: str, *, mentor_id: str) -> bool:
        """Release a claimed ticket back to the open queue.

        Returns ``True`` on success, ``False`` if the ticket does not
        exist, is not claimed, or was claimed by a different mentor.
        """
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None or ticket.status != "claimed":
                return False
            if ticket.claimed_by != mentor_id:
                return False
            ticket.status = "open"
            ticket.claimed_by = None
            ticket.claimed_by_name = None
            ticket.claimed_at = None
        return True

    def resolve(self, ticket_id: str) -> bool:
        """Mark a ticket as resolved.

        Returns ``True`` on success, ``False`` if the ticket does not
        exist or is already resolved.
        """
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None or ticket.status == "resolved":
                return False
            ticket.status = "resolved"
            ticket.resolved_at = datetime.now(timezone.utc)
        return True

    def get_open_by_channel(self, channel_id: int) -> HelpTicket | None:
        """Return the open or claimed ticket for *channel_id*, if any.

        Only tickets whose status is ``"open"`` or ``"claimed"`` are
        considered.  Returns ``None`` when no active ticket exists for
        the channel.
        """
        for ticket in self._tickets.values():
            if ticket.channel_id == channel_id and ticket.status in ("open", "claimed"):
                return ticket
        return None
