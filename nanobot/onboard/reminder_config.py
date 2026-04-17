"""Runtime configuration for the application reminder poller."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_INTEREST_GID = 502554177
DEFAULT_POLL_INTERVAL_SECONDS = 12 * 60 * 60  # 12 hours
DEFAULT_MAX_REMINDERS = 3


@dataclass(frozen=True)
class ReminderPollerConfig:
    """All runtime knobs for the reminder poller."""

    service_account_path: str
    interest_sheet_id: str
    interest_sheet_gid: int
    application_sheet_id: str
    poll_interval_seconds: int
    max_reminders: int

    @classmethod
    def from_env(cls) -> ReminderPollerConfig:
        """Build from environment variables. Required keys raise ``KeyError``."""
        return cls(
            service_account_path=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"],
            interest_sheet_id=os.environ["INTEREST_SHEET_ID"],
            interest_sheet_gid=int(os.environ.get("INTEREST_SHEET_GID", DEFAULT_INTEREST_GID)),
            application_sheet_id=os.environ["GIES_SHEET_ID"],
            poll_interval_seconds=int(
                os.environ.get("REMINDER_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
            ),
            max_reminders=int(os.environ.get("REMINDER_MAX_COUNT", DEFAULT_MAX_REMINDERS)),
        )
