"""Tests for the admin tools (send_email, send_discord, trigger_cycle).

Gating is key: all three must refuse when invoked from a non-Telegram
channel, and must succeed when invoked from Telegram.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from nanobot.agent.tools.admin import (
    SendDiscordTool,
    SendEmailTool,
    TriggerCycleTool,
)
from nanobot.bus.events import OutboundMessage


@pytest.mark.asyncio
async def test_send_email_rejects_non_telegram_channel() -> None:
    cb = AsyncMock()
    tool = SendEmailTool(send_callback=cb)
    tool.set_context(channel="discord", chat_id="1234")
    out = await tool.execute(to="a@b.com", subject="hi", body="body")
    assert "restricted to the Telegram admin channel" in out
    cb.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_email_works_from_telegram() -> None:
    cb = AsyncMock()
    tool = SendEmailTool(send_callback=cb)
    tool.set_context(channel="telegram", chat_id="8590118736")
    out = await tool.execute(
        to="alice@illinois.edu", subject="Reminder", body="Apply today"
    )
    assert "Email dispatched to alice@illinois.edu" in out
    cb.assert_awaited_once()
    sent: OutboundMessage = cb.await_args.args[0]
    assert sent.channel == "email"
    assert sent.chat_id == "alice@illinois.edu"
    assert sent.content == "Apply today"
    assert sent.metadata["subject"] == "Reminder"
    assert sent.metadata["force_send"] is True


@pytest.mark.asyncio
async def test_send_discord_rejects_non_telegram_channel() -> None:
    cb = AsyncMock()
    tool = SendDiscordTool(send_callback=cb)
    tool.set_context(channel="email", chat_id="foo@bar.com")
    out = await tool.execute(channel_id="123", content="hi")
    assert "restricted to the Telegram admin channel" in out
    cb.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_discord_works_from_telegram() -> None:
    cb = AsyncMock()
    tool = SendDiscordTool(send_callback=cb)
    tool.set_context(channel="telegram", chat_id="8590118736")
    out = await tool.execute(channel_id="1493676064239652884", content="Attention")
    assert "Posted to Discord" in out
    sent: OutboundMessage = cb.await_args.args[0]
    assert sent.channel == "discord"
    assert sent.chat_id == "1493676064239652884"
    assert sent.content == "Attention"


@pytest.mark.asyncio
async def test_trigger_cycle_rejects_non_telegram() -> None:
    tool = TriggerCycleTool()
    tool.set_context(channel="discord", chat_id="1234")
    out = await tool.execute(which="welcome")
    assert "restricted to the Telegram admin channel" in out


@pytest.mark.asyncio
async def test_trigger_cycle_rejects_unknown() -> None:
    tool = TriggerCycleTool()
    tool.set_context(channel="telegram", chat_id="8590118736")
    out = await tool.execute(which="garbage")
    assert "unknown cycle" in out
