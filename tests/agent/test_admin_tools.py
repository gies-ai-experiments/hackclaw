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


# ---------------------------------------------------------------------------
# RunWorkflowTool tests
# ---------------------------------------------------------------------------

from nanobot.agent.tools.admin import RunWorkflowTool, _resolve_path, _substitute


def test_resolve_path_simple() -> None:
    ctx = {"item": {"name": "Alice", "email": "a@b.com"}}
    assert _resolve_path("item.name", ctx) == "Alice"
    assert _resolve_path("item.email", ctx) == "a@b.com"


def test_resolve_path_list_index() -> None:
    ctx = {"apps": [{"name": "Alice"}, {"name": "Bob"}]}
    assert _resolve_path("apps[0].name", ctx) == "Alice"
    assert _resolve_path("apps[1].name", ctx) == "Bob"


def test_resolve_path_missing_returns_none() -> None:
    ctx = {"item": {"name": "Alice"}}
    assert _resolve_path("item.missing", ctx) is None
    assert _resolve_path("nothing", ctx) is None


def test_substitute_strings_and_nested_structures() -> None:
    ctx = {"item": {"name": "Alice", "email": "a@b.com"}}
    args = {
        "to": "{item.email}",
        "subject": "Hi {item.name}",
        "body": {"greeting": "Hello {item.name}!"},
        "tags": ["{item.name}", "literal"],
    }
    out = _substitute(args, ctx)
    assert out == {
        "to": "a@b.com",
        "subject": "Hi Alice",
        "body": {"greeting": "Hello Alice!"},
        "tags": ["Alice", "literal"],
    }


def test_substitute_preserves_unresolved_placeholders() -> None:
    out = _substitute("Hi {item.unknown}", {"item": {"name": "Alice"}})
    assert out == "Hi {item.unknown}"


class _FakeTool:
    def __init__(self, read_only: bool = False) -> None:
        self.read_only = read_only


class _FakeRegistry:
    """Minimal stand-in for ToolRegistry: records calls + returns canned results."""

    def __init__(
        self,
        canned: dict[str, object],
        readonly_tools: set[str] | None = None,
    ) -> None:
        self.canned = canned
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._readonly = readonly_tools or set()

    def get(self, name: str) -> _FakeTool | None:
        if name in self.canned or name in self._readonly:
            return _FakeTool(read_only=name in self._readonly)
        return None

    async def execute(self, name: str, params: dict[str, object]) -> object:
        self.calls.append((name, params))
        return self.canned.get(name, f"ok:{name}")


@pytest.mark.asyncio
async def test_run_workflow_rejects_non_telegram() -> None:
    reg = _FakeRegistry({})
    tool = RunWorkflowTool(registry=reg)
    tool.set_context(channel="discord", chat_id="x")
    out = await tool.execute(plan=[{"tool": "send_email", "args": {}}])
    assert "restricted to the Telegram" in out


@pytest.mark.asyncio
async def test_run_workflow_dry_run_calls_readonly_but_not_writes() -> None:
    reg = _FakeRegistry(
        {"list_applicants": '[{"name":"Alice","email":"a@b.com"}]'},
        readonly_tools={"list_applicants"},
    )
    tool = RunWorkflowTool(registry=reg)
    tool.set_context(channel="telegram", chat_id="1")

    plan = [
        {"tool": "list_applicants", "id": "apps"},
        {
            "tool": "send_email",
            "for_each": "apps",
            "args": {"to": "{item.email}", "subject": "hi", "body": "Hi {item.name}"},
        },
    ]
    out = await tool.execute(plan=plan, dry_run=True)

    # Read-only list_applicants fired so apps is populated. send_email did NOT fire.
    assert [c[0] for c in reg.calls] == ["list_applicants"]
    assert "DRY-RUN" in out
    assert "would call send_email" in out
    assert "a@b.com" in out  # confirms for_each substitution ran


@pytest.mark.asyncio
async def test_run_workflow_live_executes_and_substitutes() -> None:
    reg = _FakeRegistry(
        {"list_applicants": '[{"name":"Alice","email":"a@b.com"}]'},
        readonly_tools={"list_applicants"},
    )
    tool = RunWorkflowTool(registry=reg)
    tool.set_context(channel="telegram", chat_id="1")

    plan = [
        {"tool": "list_applicants", "id": "apps"},
        {
            "tool": "send_email",
            "for_each": "apps",
            "args": {"to": "{item.email}", "subject": "hi", "body": "Hi {item.name}"},
        },
    ]
    out = await tool.execute(plan=plan, dry_run=False)

    assert len(reg.calls) == 2
    assert reg.calls[0] == ("list_applicants", {})
    assert reg.calls[1] == (
        "send_email",
        {"to": "a@b.com", "subject": "hi", "body": "Hi Alice"},
    )
    assert "LIVE" in out


@pytest.mark.asyncio
async def test_run_workflow_rejects_recursion() -> None:
    tool = RunWorkflowTool(registry=_FakeRegistry({}))
    tool.set_context(channel="telegram", chat_id="1")
    out = await tool.execute(plan=[{"tool": "run_workflow", "args": {"plan": []}}])
    assert "no recursion" in out


@pytest.mark.asyncio
async def test_run_workflow_errors_on_missing_for_each_source() -> None:
    tool = RunWorkflowTool(registry=_FakeRegistry({}))
    tool.set_context(channel="telegram", chat_id="1")
    plan = [{"tool": "send_email", "for_each": "nonexistent", "args": {}}]
    out = await tool.execute(plan=plan, dry_run=True)
    assert "not a saved list" in out


# ---------------------------------------------------------------------------
# ListApplicantsTool gate test (real sheet reads aren't unit-tested)
# ---------------------------------------------------------------------------

from nanobot.agent.tools.admin import ListApplicantsTool


@pytest.mark.asyncio
async def test_list_applicants_rejects_non_telegram() -> None:
    tool = ListApplicantsTool()
    tool.set_context(channel="discord", chat_id="x")
    out = await tool.execute(limit=0)
    assert "restricted to the Telegram" in out


@pytest.mark.asyncio
async def test_list_applicants_requires_env() -> None:
    import os
    # Clear the env vars for this test
    saved = {k: os.environ.pop(k, None) for k in ("GOOGLE_SERVICE_ACCOUNT_JSON", "GIES_SHEET_ID")}
    try:
        tool = ListApplicantsTool()
        tool.set_context(channel="telegram", chat_id="1")
        out = await tool.execute(limit=0)
        assert "not configured" in out
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
