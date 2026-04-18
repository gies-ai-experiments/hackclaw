"""Tests for the Telegram channel."""
from __future__ import annotations

import pytest

from nanobot.channels.telegram import TelegramChannel, TelegramConfig


def test_config_defaults() -> None:
    cfg = TelegramConfig()
    assert cfg.enabled is False
    assert cfg.token == ""
    assert cfg.group_policy == "mention"
    assert cfg.allow_from == []


def test_default_config_class_method() -> None:
    d = TelegramChannel.default_config()
    assert d["enabled"] is False
    assert d["groupPolicy"] == "mention"
    assert d["allowFrom"] == []


def test_parse_chat_id_numeric() -> None:
    assert TelegramChannel._parse_chat_id("-1001234567890") == -1001234567890
    assert TelegramChannel._parse_chat_id("42") == 42


def test_parse_chat_id_username_passthrough() -> None:
    assert TelegramChannel._parse_chat_id("@myuser") == "@myuser"


def test_parse_chat_id_empty_raises() -> None:
    with pytest.raises(ValueError):
        TelegramChannel._parse_chat_id("")


def test_config_accepts_camelcase_from_config_json() -> None:
    """Config loader feeds camelCase keys; pydantic alias gen should accept them."""
    cfg = TelegramConfig(
        enabled=True,
        token="xxx",
        allowFrom=["123"],
        groupPolicy="open",
    )
    assert cfg.enabled is True
    assert cfg.allow_from == ["123"]
    assert cfg.group_policy == "open"
