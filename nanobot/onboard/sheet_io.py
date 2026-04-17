"""Thin gspread wrapper for the reminder poller.

Kept narrow on purpose: ``fetch_rows`` returns a flat list-of-lists so the
caller can be tested with no gspread dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from loguru import logger

# Column numbers (1-based, gspread style) on the interest tab.
COL_REMINDER_COUNT = 12      # L
COL_LAST_REMINDER_AT = 13    # M
COL_NOT_ELIGIBLE_AT = 14     # N

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def open_client(service_account_path: str) -> gspread.Client:
    """Authorize a gspread client from a service-account JSON file."""
    creds = Credentials.from_service_account_file(service_account_path, scopes=_SCOPES)
    return gspread.authorize(creds)


def open_worksheet_by_gid(client: gspread.Client, sheet_id: str, gid: int) -> Any:
    """Open a worksheet by its numeric ``gid`` (Google sheet tab id)."""
    spreadsheet = client.open_by_key(sheet_id)
    for ws in spreadsheet.worksheets():
        if ws.id == gid:
            return ws
    raise LookupError(f"No worksheet with gid={gid} in spreadsheet {sheet_id}")


def open_first_worksheet(client: gspread.Client, sheet_id: str) -> Any:
    """Open the default (first) worksheet of *sheet_id*."""
    return client.open_by_key(sheet_id).sheet1


def fetch_rows(worksheet: Any) -> list[list[str]]:
    """Return all rows after the header as ``list[list[str]]``."""
    values = worksheet.get_all_values()
    if not values:
        return []
    return values[1:]


@dataclass
class SheetWriter:
    """Minimal writer wrapping a worksheet for cell updates."""

    worksheet: Any

    def _sheet_row(self, data_row_index: int) -> int:
        """Convert a 1-based data row index to the sheet's 1-based row.

        Row 1 in the sheet is the header, so data row 1 is sheet row 2.
        """
        return data_row_index + 1


def stamp_reminder(
    writer: SheetWriter, *, row_index: int, new_count: int, now: datetime
) -> None:
    """Bump ``Reminder Count`` and ``Last Reminder At`` for one row."""
    sheet_row = writer._sheet_row(row_index)
    writer.worksheet.update_cell(sheet_row, COL_REMINDER_COUNT, new_count)
    writer.worksheet.update_cell(
        sheet_row, COL_LAST_REMINDER_AT, now.isoformat(timespec="seconds")
    )
    logger.debug(
        "Stamped reminder row={} sheet_row={} new_count={}",
        row_index,
        sheet_row,
        new_count,
    )


def stamp_not_eligible(writer: SheetWriter, *, row_index: int, now: datetime) -> None:
    """Stamp ``Not Eligible Sent At`` for one row (one-shot)."""
    sheet_row = writer._sheet_row(row_index)
    writer.worksheet.update_cell(
        sheet_row, COL_NOT_ELIGIBLE_AT, now.isoformat(timespec="seconds")
    )
    logger.debug(
        "Stamped not-eligible row={} sheet_row={}", row_index, sheet_row,
    )
