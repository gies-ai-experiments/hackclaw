"""Admin tools — cross-channel actions triggered from Telegram.

These tools let an admin DM the Telegram bot and instruct the agent to
reach into *other* channels (send Discord messages, send Gmail, manually
fire the welcome/reminder systemd timers). Gated on channel ``telegram``
so Discord/email chatter can't escalate.

The channel gate works because :meth:`nanobot.agent.loop.AgentLoop` calls
``set_context(channel, chat_id)`` on every registered tool before each
turn; we record ``channel`` and refuse to execute when it isn't
``telegram``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    IntegerSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.bus.events import OutboundMessage


_TELEGRAM_ONLY_ERROR = (
    "Error: this tool is restricted to the Telegram admin channel. "
    "Ignore and respond normally."
)


class _AdminTool(Tool):
    """Mixin: records the current conversation's channel + sender for gating."""

    def __init__(self) -> None:
        self._channel = ""
        self._chat_id = ""

    def set_context(self, channel: str, chat_id: str) -> None:
        self._channel = channel
        self._chat_id = chat_id

    def _gate(self) -> str | None:
        """Return an error string if this turn didn't originate from Telegram."""
        if self._channel != "telegram":
            return _TELEGRAM_ONLY_ERROR
        return None


@tool_parameters(
    tool_parameters_schema(
        to=StringSchema("Recipient email address."),
        subject=StringSchema("Email subject line."),
        body=StringSchema("Email body (plain text)."),
        required=["to", "subject", "body"],
    )
)
class SendEmailTool(_AdminTool):
    """Send an email via the configured SMTP channel."""

    def __init__(self, send_callback: Any) -> None:
        super().__init__()
        self._send_callback = send_callback

    @property
    def name(self) -> str:
        return "send_email"

    @property
    def description(self) -> str:
        return (
            "Send an email via the configured Gmail channel (SMTP). "
            "Use for announcements, reminders, or 1-to-1 replies from admin. "
            "Only callable from the Telegram admin channel."
        )

    async def execute(self, *, to: str, subject: str, body: str, **_: Any) -> str:
        err = self._gate()
        if err:
            return err
        msg = OutboundMessage(
            channel="email",
            chat_id=to.strip(),
            content=body,
            metadata={"subject": subject, "force_send": True},
        )
        try:
            await self._send_callback(msg)
        except Exception as exc:
            return f"Error sending email to {to}: {exc}"
        return f"Email dispatched to {to} (subject: {subject!r})"


@tool_parameters(
    tool_parameters_schema(
        channel_id=StringSchema(
            "Numeric Discord channel ID (e.g. '1493676064239652884'). "
            "Ask the user for this ID if you don't know it."
        ),
        content=StringSchema("Message content (plaintext, <=2000 chars)."),
        required=["channel_id", "content"],
    )
)
class SendDiscordTool(_AdminTool):
    """Post a message into a Discord channel on the connected guild."""

    def __init__(self, send_callback: Any) -> None:
        super().__init__()
        self._send_callback = send_callback

    @property
    def name(self) -> str:
        return "send_discord"

    @property
    def description(self) -> str:
        return (
            "Post a message into a specific Discord channel by numeric ID. "
            "Only callable from the Telegram admin channel."
        )

    async def execute(self, *, channel_id: str, content: str, **_: Any) -> str:
        err = self._gate()
        if err:
            return err
        msg = OutboundMessage(
            channel="discord",
            chat_id=channel_id.strip(),
            content=content,
        )
        try:
            await self._send_callback(msg)
        except Exception as exc:
            return f"Error posting to Discord channel {channel_id}: {exc}"
        return f"Posted to Discord channel {channel_id}"


@tool_parameters(
    tool_parameters_schema(
        which=StringSchema(
            "Which cycle to trigger: 'welcome' (hourly first-touch) or "
            "'reminder' (6h apply-reminder).",
            enum=["welcome", "reminder"],
        ),
        required=["which"],
    )
)
class TriggerCycleTool(_AdminTool):
    """Manually fire one of the scheduled systemd services now (off-cycle)."""

    @property
    def name(self) -> str:
        return "trigger_cycle"

    @property
    def description(self) -> str:
        return (
            "Manually trigger one of the reminder systemd timers now, "
            "instead of waiting for the next scheduled fire. "
            "'welcome' = hackclaw-welcome.service (welcome new interest-form signups). "
            "'reminder' = hackclaw-reminder.service (apply-reminder for Gies who haven't applied). "
            "Only callable from the Telegram admin channel."
        )

    async def execute(self, *, which: str, **_: Any) -> str:
        err = self._gate()
        if err:
            return err
        service = {"welcome": "hackclaw-welcome.service", "reminder": "hackclaw-reminder.service"}.get(
            which.strip().lower()
        )
        if service is None:
            return f"Error: unknown cycle {which!r}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "start", service,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err_out = await proc.communicate()
            if proc.returncode != 0:
                return f"Error starting {service}: {err_out.decode().strip()}"
        except Exception as exc:
            return f"Error triggering {service}: {exc}"
        logger.info("Admin triggered {} off-cycle", service)
        return f"Triggered {service}. Check logs/{which}.log in ~15s for result."


@tool_parameters(
    tool_parameters_schema(
        limit=IntegerSchema(
            0,
            description="Max members to return (0 = all). Use small values while drafting to keep tokens low.",
            minimum=0,
        ),
    )
)
class ListApplicantsTool(_AdminTool):
    """Read the Gies application-form sheet and return every unique applicant."""

    @property
    def name(self) -> str:
        return "list_applicants"

    @property
    def description(self) -> str:
        return (
            "Read the Gies AI for Impact Challenge application sheet and return "
            "a JSON list of unique applicants as [{name, email, team}]. "
            "Every person listed on any team counts as an applicant; emails are "
            "canonicalized to @illinois.edu and duplicates dropped. "
            "Only callable from the Telegram admin channel."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, *, limit: int = 0, **_: Any) -> str:
        err = self._gate()
        if err:
            return err

        sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        app_id = os.environ.get("GIES_SHEET_ID", "")
        if not sa or not app_id:
            return "Error: GOOGLE_SERVICE_ACCOUNT_JSON or GIES_SHEET_ID not configured"

        # Imported lazily so module import is cheap when not running.
        from nanobot.onboard.email_canonical import canonical_illinois_email
        from nanobot.onboard.parser import extract_members
        from nanobot.onboard.sheet_io import fetch_rows, open_client, open_first_worksheet

        try:
            client = open_client(sa)
            ws = open_first_worksheet(client, app_id)
            rows = fetch_rows(ws)
        except Exception as exc:
            return f"Error reading application sheet: {exc}"

        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for r in rows:
            team = r[1] if len(r) > 1 else ""
            trimmed = r[1:] if r else r  # drop Timestamp col before extract_members
            for m in extract_members(trimmed):
                canon = canonical_illinois_email(m.email)
                if not canon or canon in seen:
                    continue
                seen.add(canon)
                out.append({"name": m.name, "email": canon, "team": team})
                if limit and len(out) >= limit:
                    return json.dumps(out)
        return json.dumps(out)


_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_.\[\]]*)\}")


def _resolve_path(path: str, ctx: dict[str, Any]) -> Any:
    """Walk dotted path like ``item.email`` or ``applicants[0].name`` in *ctx*."""
    # Split on dots but also respect [N] index steps.
    parts: list[str | int] = []
    for chunk in path.split("."):
        m = re.match(r"^([^\[]+)((?:\[\d+\])*)$", chunk)
        if not m:
            return None
        parts.append(m.group(1))
        for idx in re.findall(r"\[(\d+)\]", m.group(2)):
            parts.append(int(idx))

    val: Any = ctx
    for p in parts:
        if isinstance(p, int):
            if isinstance(val, list) and 0 <= p < len(val):
                val = val[p]
            else:
                return None
        else:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                return None
            if val is None:
                return None
    return val


def _substitute(obj: Any, ctx: dict[str, Any]) -> Any:
    """Recursively replace ``{var.path}`` in strings using *ctx*."""
    if isinstance(obj, str):
        def repl(m: re.Match[str]) -> str:
            v = _resolve_path(m.group(1), ctx)
            return str(v) if v is not None else m.group(0)
        return _VAR_RE.sub(repl, obj)
    if isinstance(obj, dict):
        return {k: _substitute(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(x, ctx) for x in obj]
    return obj


@tool_parameters(
    tool_parameters_schema(
        plan=ArraySchema(
            ObjectSchema(
                {
                    "tool": StringSchema("Name of a registered tool to call."),
                    "args": ObjectSchema({}, description="Tool args. Supports {var.path} substitution."),
                    "id": StringSchema("Optional: save the result under this name for later steps."),
                    "for_each": StringSchema("Optional: name of a saved list; run once per item with {item.*} bound."),
                },
                required=["tool"],
            ),
            description="Ordered list of steps. Each step calls a registered tool.",
        ),
        dry_run=BooleanSchema(
            description="When true, don't invoke tools; return what would happen. Default true for safety.",
            default=True,
        ),
        required=["plan"],
    )
)
class RunWorkflowTool(_AdminTool):
    """Compose calls across existing tools: list_applicants → for_each → send_email, etc.

    **Always call with dry_run=true first**, show the preview to the admin,
    then re-call with dry_run=false after they confirm.
    """

    def __init__(self, registry: Any) -> None:
        super().__init__()
        self._registry = registry

    @property
    def name(self) -> str:
        return "run_workflow"

    @property
    def description(self) -> str:
        return (
            "Execute a multi-step plan composed of other registered tools. "
            "Each step is {tool, args, id?, for_each?}. Use 'id' to save a "
            "result for later steps; use 'for_each': <saved-list-id> to loop. "
            "Inside args, '{item.email}' is replaced per-iteration and "
            "'{saved_id.field}' pulls from earlier steps. "
            "Set dry_run=true FIRST to preview; re-run with dry_run=false "
            "only after the admin confirms. Telegram-only."
        )

    async def execute(self, *, plan: list[dict[str, Any]], dry_run: bool = True, **_: Any) -> str:
        err = self._gate()
        if err:
            return err
        if not isinstance(plan, list) or not plan:
            return "Error: plan must be a non-empty list of steps"

        variables: dict[str, Any] = {}
        lines: list[str] = [f"Workflow ({'DRY-RUN' if dry_run else 'LIVE'}) — {len(plan)} step(s):"]

        for idx, step in enumerate(plan, start=1):
            tool_name = step.get("tool")
            if not tool_name:
                return f"Error: step {idx} missing 'tool'"
            if tool_name == "run_workflow":
                return f"Error: step {idx} cannot call run_workflow (no recursion)"

            args = step.get("args", {}) or {}
            for_each_key = step.get("for_each")
            save_as = step.get("id")

            # Read-only tools are safe during dry-run (and required, so downstream
            # for_each loops have real data to iterate over).
            tool_obj = None
            if hasattr(self._registry, "get"):
                tool_obj = self._registry.get(tool_name)
            is_readonly = bool(tool_obj and getattr(tool_obj, "read_only", False))
            should_execute = (not dry_run) or is_readonly

            if for_each_key:
                items = variables.get(for_each_key)
                if not isinstance(items, list):
                    return (
                        f"Error: step {idx} for_each='{for_each_key}' is not a saved list. "
                        f"Available: {sorted(variables.keys())}"
                    )
                lines.append(f"  step {idx}: {tool_name} × {len(items)} (for_each={for_each_key})")
                preview_shown = 0
                failures = 0
                for item in items:
                    resolved = _substitute(args, {**variables, "item": item})
                    if not should_execute:
                        if preview_shown < 3:
                            lines.append(f"    would call {tool_name}({resolved})")
                            preview_shown += 1
                    else:
                        out = await self._registry.execute(tool_name, resolved)
                        if isinstance(out, str) and out.startswith("Error"):
                            failures += 1
                if not should_execute and len(items) > 3:
                    lines.append(f"    … {len(items) - 3} more")
                if should_execute:
                    lines.append(f"    sent={len(items) - failures}, failed={failures}")

            else:
                resolved = _substitute(args, variables)
                if not should_execute:
                    lines.append(f"  step {idx}: would call {tool_name}({resolved})")
                    if save_as:
                        lines.append(f"    (would save as {save_as!r})")
                else:
                    out = await self._registry.execute(tool_name, resolved)
                    if save_as:
                        # Best effort: if the tool returned JSON, parse it so for_each works.
                        parsed: Any = out
                        if isinstance(out, str):
                            try:
                                parsed = json.loads(out)
                            except (json.JSONDecodeError, ValueError):
                                parsed = out
                        variables[save_as] = parsed
                    summary = out if isinstance(out, str) else json.dumps(out)[:200]
                    prefix = "  step {}: ".format(idx)
                    if dry_run and is_readonly:
                        prefix += "(read-only, executed in dry-run) "
                    lines.append(f"{prefix}{tool_name} → {summary[:200]}")

        if dry_run:
            lines.append(
                "\nThis was a DRY-RUN. Nothing mutating was sent. "
                "Re-call run_workflow with the same plan and dry_run=false to execute."
            )
        return "\n".join(lines)
