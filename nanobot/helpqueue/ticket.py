"""HelpTicket data model and in-memory TicketStore for the help-queue system."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

# Mode of help a participant requested.
HelpMode = Literal["in_person", "online"]


@dataclass
class HelpTicket:
    """A single help ticket submitted by a hackathon team."""

    id: str                             # e.g. "HELP-001"
    team_name: str
    channel_id: int
    location: str
    description: str
    status: str = "open"                # open | claimed | resolved
    mode: HelpMode = "in_person"        # in_person (mentor goes to table) | online (voice room)
    participant_id: str | None = None   # Discord user id of the requester (for online DMs + perm grants)
    online_room_id: str | None = None   # When online + claimed: which voice channel is assigned
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    claimed_by: str | None = None       # mentor Discord ID
    claimed_by_name: str | None = None  # mentor display name
    claimed_at: datetime | None = None
    resolved_at: datetime | None = None
    queue_message_id: int | None = None
    queue_channel_id: int | None = None  # Which queue channel the claim/resolve UI lives in.
    track: str | None = None             # For /mentorme tickets: the mentor track (e.g. "finance").
    solution: str | None = None
    granted_user_ids: list[str] = field(default_factory=list)
    """Discord user ids who received a view+connect override on ``online_room_id``
    while this ticket was claimed. Populated on claim (every member of the
    team's text channel), consumed on resolve/unclaim to revoke in bulk."""


class TicketStore:
    """Thread-safe, in-memory store for :class:`HelpTicket` instances.

    Tickets are auto-assigned sequential IDs of the form ``HELP-001``,
    ``HELP-002``, etc.

    For ``mode="online"`` tickets this store also holds two pieces of
    shared state:

    - ``_online_queue``: FIFO list of ticket ids waiting for a mentor.
    - ``_rooms``: a small pool of voice channel ids → currently-assigned
      ticket id (or ``None`` when free). Rooms are injected at startup
      via :meth:`configure_rooms` so this module stays decoupled from
      Discord config.
    """

    def __init__(self) -> None:
        self._tickets: dict[str, HelpTicket] = {}
        self._counter: int = 0
        self._lock = threading.Lock()
        self._online_queue: list[str] = []       # ordered ticket ids awaiting a room
        self._rooms: dict[str, str | None] = {}  # voice_channel_id → ticket_id or None

    # ------------------------------------------------------------------
    # Room pool configuration
    # ------------------------------------------------------------------

    def configure_rooms(self, room_ids: list[str]) -> None:
        """Register voice-channel ids as the pool of online office-hours rooms.

        Called once at bot startup. Rooms are initialized as free. Calling
        again replaces the pool (unused rooms drop; new ones become free).
        Rooms currently assigned to an active ticket keep their assignment
        if still present in *room_ids*.
        """
        with self._lock:
            new_rooms: dict[str, str | None] = {}
            for rid in room_ids:
                rid = str(rid)
                new_rooms[rid] = self._rooms.get(rid)
            self._rooms = new_rooms

    def configured_rooms(self) -> list[str]:
        """Return configured voice-channel ids (in configuration order)."""
        return list(self._rooms.keys())

    # ------------------------------------------------------------------
    # Public API — ticket lifecycle
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        team_name: str,
        channel_id: int,
        location: str,
        description: str,
        mode: HelpMode = "in_person",
        participant_id: str | None = None,
    ) -> HelpTicket:
        """Create a new ticket and return it.

        Online tickets are NOT automatically enqueued — the caller should
        decide whether to enqueue via :meth:`enqueue_online` (usually yes,
        but deferred for the solution-suggestion flow).
        """
        with self._lock:
            self._counter += 1
            ticket_id = f"HELP-{self._counter:03d}"
            ticket = HelpTicket(
                id=ticket_id,
                team_name=team_name,
                channel_id=channel_id,
                location=location,
                description=description,
                mode=mode,
                participant_id=participant_id,
            )
            self._tickets[ticket_id] = ticket
        return ticket

    def get(self, ticket_id: str) -> HelpTicket | None:
        """Return a ticket by its ID, or ``None`` if not found."""
        return self._tickets.get(ticket_id)

    def claim(self, ticket_id: str, *, mentor_id: str, mentor_name: str) -> bool:
        """Claim an open ticket on behalf of a mentor.

        Returns ``True`` on success, ``False`` if the ticket does not
        exist or is not in the ``"open"`` state. For online tickets the
        caller is responsible for reserving a room via :meth:`reserve_room`
        before (or after) calling this — :meth:`claim` itself only mutates
        ticket state, not the room pool.
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
        """Release a claimed ticket back to the open queue."""
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
            # Caller handles room release + re-enqueue for online tickets.
        return True

    def resolve(self, ticket_id: str) -> bool:
        """Mark a ticket as resolved."""
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None or ticket.status == "resolved":
                return False
            ticket.status = "resolved"
            ticket.resolved_at = datetime.now(timezone.utc)
        return True

    def get_open_by_channel(self, channel_id: int) -> HelpTicket | None:
        """Return the open or claimed ticket for *channel_id*, if any."""
        for ticket in self._tickets.values():
            if ticket.channel_id == channel_id and ticket.status in ("open", "claimed"):
                return ticket
        return None

    # ------------------------------------------------------------------
    # Online queue
    # ------------------------------------------------------------------

    def enqueue_online(self, ticket_id: str) -> int:
        """Append a ticket to the online queue and return its 1-based position.

        Called when a participant submits ``/helpme mode:online``.
        Returns ``-1`` if the ticket doesn't exist.
        """
        with self._lock:
            if ticket_id not in self._tickets:
                return -1
            if ticket_id not in self._online_queue:
                self._online_queue.append(ticket_id)
            return self._online_queue.index(ticket_id) + 1

    def pop_online(self, ticket_id: str) -> None:
        """Remove *ticket_id* from the online queue. Idempotent."""
        with self._lock:
            if ticket_id in self._online_queue:
                self._online_queue.remove(ticket_id)

    def online_position(self, ticket_id: str) -> int:
        """Return 1-based position of *ticket_id* in the online queue, or -1."""
        with self._lock:
            if ticket_id not in self._online_queue:
                return -1
            return self._online_queue.index(ticket_id) + 1

    def online_queue_snapshot(self) -> list[str]:
        """Return a snapshot of the current online queue as a list of ticket ids."""
        with self._lock:
            return list(self._online_queue)

    # ------------------------------------------------------------------
    # Room pool
    # ------------------------------------------------------------------

    def reserve_room(
        self,
        ticket_id: str,
        *,
        prefer_room: str | None = None,
    ) -> str | None:
        """Reserve a free voice-channel room for a ticket.

        Returns the reserved room id, or ``None`` if no rooms are free.
        *prefer_room* (e.g. the mentor's current voice channel) is picked
        if it's configured and free; otherwise the first free room wins.
        The reservation is recorded both in the pool AND on the ticket
        (``online_room_id``).
        """
        with self._lock:
            if ticket_id not in self._tickets:
                return None
            free = [rid for rid, tid in self._rooms.items() if tid is None]
            if not free:
                return None
            chosen: str
            if prefer_room is not None and str(prefer_room) in free:
                chosen = str(prefer_room)
            else:
                chosen = free[0]
            self._rooms[chosen] = ticket_id
            self._tickets[ticket_id].online_room_id = chosen
            return chosen

    def release_room(self, ticket_id: str) -> str | None:
        """Release the room currently assigned to *ticket_id*.

        Returns the room id that was released (for the caller to revoke
        permission overrides on), or ``None`` if the ticket had no room.
        """
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                return None
            room = ticket.online_room_id
            if room is None:
                return None
            if self._rooms.get(room) == ticket_id:
                self._rooms[room] = None
            ticket.online_room_id = None
            return room

    def any_room_free(self) -> bool:
        """True if at least one voice-channel room is free right now."""
        with self._lock:
            return any(tid is None for tid in self._rooms.values())

    def rooms_snapshot(self) -> dict[str, str | None]:
        """Debug snapshot of the room pool (room_id → ticket_id|None)."""
        with self._lock:
            return dict(self._rooms)
