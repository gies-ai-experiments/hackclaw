"""Tests for HelpQueueConfig and its integration with DiscordConfig."""

from __future__ import annotations

from nanobot.channels.discord import DiscordConfig, HelpQueueConfig


def test_help_queue_config_defaults() -> None:
    """HelpQueueConfig should have sensible defaults."""
    cfg = HelpQueueConfig()
    assert cfg.channel_id == ""
    assert cfg.mentor_role_id == ""
    assert cfg.reminder_minutes == 10


def test_discord_config_includes_help_queue() -> None:
    """DiscordConfig should accept camelCase help_queue JSON and round-trip correctly."""
    raw = {
        "enabled": True,
        "helpQueue": {
            "channelId": "123",
            "mentorRoleId": "456",
            "reminderMinutes": 5,
        },
    }
    cfg = DiscordConfig.model_validate(raw)
    assert cfg.help_queue.channel_id == "123"
    assert cfg.help_queue.mentor_role_id == "456"
    assert cfg.help_queue.reminder_minutes == 5

    # Round-trip: dump with aliases and re-validate
    dumped = cfg.model_dump(by_alias=True)
    assert "helpQueue" in dumped
    assert dumped["helpQueue"]["channelId"] == "123"
    assert dumped["helpQueue"]["mentorRoleId"] == "456"
    assert dumped["helpQueue"]["reminderMinutes"] == 5


def test_discord_config_default_help_queue() -> None:
    """DiscordConfig should work without specifying help_queue at all."""
    cfg = DiscordConfig(enabled=True)
    assert cfg.help_queue.channel_id == ""
    assert cfg.help_queue.mentor_role_id == ""
    assert cfg.help_queue.reminder_minutes == 10
