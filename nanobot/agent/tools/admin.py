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
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema
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
