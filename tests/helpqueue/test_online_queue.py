"""Tests for the online-mode extensions to TicketStore."""
from __future__ import annotations

from nanobot.helpqueue.ticket import TicketStore


def _new_ticket(store: TicketStore, team: str, mode: str = "online") -> str:
    t = store.create(
        team_name=team,
        channel_id=12345,
        location="l",
        description="d",
        mode=mode,  # type: ignore[arg-type]
        participant_id="u" + team,
    )
    return t.id


def test_configure_rooms_registers_ids_as_free() -> None:
    s = TicketStore()
    s.configure_rooms(["111", "222"])
    assert s.configured_rooms() == ["111", "222"]
    assert s.any_room_free()
    assert s.rooms_snapshot() == {"111": None, "222": None}


def test_configure_rooms_preserves_in_flight_assignments() -> None:
    s = TicketStore()
    s.configure_rooms(["111", "222"])
    tid = _new_ticket(s, "A")
    s.reserve_room(tid)
    # Reconfiguring with the same pool should NOT evict an in-flight reservation
    s.configure_rooms(["111", "222"])
    assert s.rooms_snapshot()["111"] == tid or s.rooms_snapshot()["222"] == tid


def test_enqueue_returns_position_and_is_idempotent() -> None:
    s = TicketStore()
    s.configure_rooms(["111"])
    a = _new_ticket(s, "A")
    b = _new_ticket(s, "B")
    assert s.enqueue_online(a) == 1
    assert s.enqueue_online(b) == 2
    # Re-enqueueing an already-queued ticket preserves its position, no duplication
    assert s.enqueue_online(a) == 1
    assert s.online_queue_snapshot() == [a, b]


def test_pop_removes_from_queue_and_is_idempotent() -> None:
    s = TicketStore()
    s.configure_rooms(["111"])
    a = _new_ticket(s, "A")
    s.enqueue_online(a)
    s.pop_online(a)
    assert s.online_queue_snapshot() == []
    s.pop_online(a)  # no-op, no raise
    assert s.online_position(a) == -1


def test_reserve_room_prefers_requested_when_free() -> None:
    s = TicketStore()
    s.configure_rooms(["111", "222"])
    a = _new_ticket(s, "A")
    assert s.reserve_room(a, prefer_room="222") == "222"
    assert s.rooms_snapshot() == {"111": None, "222": a}


def test_reserve_room_falls_back_when_preferred_busy() -> None:
    s = TicketStore()
    s.configure_rooms(["111", "222"])
    a = _new_ticket(s, "A")
    b = _new_ticket(s, "B")
    s.reserve_room(a, prefer_room="222")
    # 222 is now busy; prefer_room=222 falls back to 111
    assert s.reserve_room(b, prefer_room="222") == "111"


def test_reserve_room_returns_none_when_pool_exhausted() -> None:
    s = TicketStore()
    s.configure_rooms(["111"])
    a = _new_ticket(s, "A")
    b = _new_ticket(s, "B")
    s.reserve_room(a)
    assert s.reserve_room(b) is None


def test_release_room_frees_the_slot_and_clears_ticket_ref() -> None:
    s = TicketStore()
    s.configure_rooms(["111"])
    a = _new_ticket(s, "A")
    s.reserve_room(a)
    released = s.release_room(a)
    assert released == "111"
    assert s.rooms_snapshot() == {"111": None}
    assert s.get(a).online_room_id is None


def test_release_room_is_noop_for_unreserved_ticket() -> None:
    s = TicketStore()
    s.configure_rooms(["111"])
    a = _new_ticket(s, "A")
    assert s.release_room(a) is None


def test_in_person_ticket_ignored_by_online_queue() -> None:
    s = TicketStore()
    s.configure_rooms(["111"])
    a = _new_ticket(s, "A", mode="in_person")
    # We can still enqueue an in-person id, but nothing forces us to — the
    # caller decides. The test here just asserts defaults: no auto-enqueue.
    assert s.online_queue_snapshot() == []
    assert s.online_position(a) == -1


def test_mode_field_defaults_to_in_person() -> None:
    s = TicketStore()
    t = s.create(
        team_name="X", channel_id=1, location="BIF 2001", description="need help",
    )
    assert t.mode == "in_person"
    assert t.participant_id is None
    assert t.online_room_id is None
    assert t.granted_user_ids == []


def test_granted_user_ids_is_independent_per_ticket() -> None:
    """Mutating one ticket's granted list must not bleed into another's default."""
    s = TicketStore()
    a = _new_ticket(s, "A")
    b = _new_ticket(s, "B")
    s.get(a).granted_user_ids.append("user-1")
    assert s.get(a).granted_user_ids == ["user-1"]
    assert s.get(b).granted_user_ids == []
