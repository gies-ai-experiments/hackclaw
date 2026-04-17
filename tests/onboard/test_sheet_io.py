"""Tests for the sheet I/O wrapper. Uses a hand-rolled fake worksheet."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from nanobot.onboard.sheet_io import (
    SheetWriter,
    fetch_rows,
    stamp_not_eligible,
    stamp_reminder,
)


def test_fetch_rows_strips_header() -> None:
    ws = MagicMock()
    ws.get_all_values.return_value = [
        ["Header1", "Header2"],
        ["a", "1"],
        ["b", "2"],
    ]
    rows = fetch_rows(ws)
    assert rows == [["a", "1"], ["b", "2"]]


def test_stamp_reminder_writes_three_cells() -> None:
    ws = MagicMock()
    writer = SheetWriter(ws)
    now = datetime(2026, 4, 17, 12, 0, 0)
    stamp_reminder(writer, row_index=5, new_count=2, now=now)

    # row_index 5 is the 5th data row. Header occupies row 1, so the sheet
    # row to update is 5 + 1 = 6 (1-based). Columns L (12) and M (13).
    ws.update_cell.assert_any_call(6, 12, 2)
    ws.update_cell.assert_any_call(6, 13, "2026-04-17T12:00:00")
    assert ws.update_cell.call_count == 2


def test_stamp_not_eligible_writes_one_cell() -> None:
    ws = MagicMock()
    writer = SheetWriter(ws)
    now = datetime(2026, 4, 17, 12, 0, 0)
    stamp_not_eligible(writer, row_index=4, now=now)
    ws.update_cell.assert_called_once_with(5, 14, "2026-04-17T12:00:00")
