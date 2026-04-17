"""Tests for ReminderPollerConfig env loading."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from nanobot.onboard.reminder_config import ReminderPollerConfig


def test_from_env_defaults() -> None:
    env = {
        "GOOGLE_SERVICE_ACCOUNT_JSON": "/tmp/sa.json",
        "INTEREST_SHEET_ID": "INT123",
        "GIES_SHEET_ID": "APP456",
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = ReminderPollerConfig.from_env()
    assert cfg.service_account_path == "/tmp/sa.json"
    assert cfg.interest_sheet_id == "INT123"
    assert cfg.interest_sheet_gid == 502554177
    assert cfg.application_sheet_id == "APP456"
    assert cfg.poll_interval_seconds == 43200
    assert cfg.max_reminders == 3


def test_from_env_missing_required_raises() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(KeyError):
            ReminderPollerConfig.from_env()
