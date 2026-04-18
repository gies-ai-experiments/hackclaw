"""Telegram channel implementation using python-telegram-bot.

Mirrors the shape of :mod:`nanobot.channels.discord`:
- Long-polls the Telegram getUpdates endpoint (no webhook → no public URL needed).
- ``group_policy: "mention"`` only responds when @-mentioned inside groups;
  DMs always get a response.
- Outbound messages go through the ``send`` method; the outbound dispatcher
  in :mod:`nanobot.channels.manager` handles retries.

Text-only in this version; audio/image attachments are a future upgrade.
"""

from __future__ import annotations

import asyncio
import importlib.util
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from pydantic import Field

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import Base
from nanobot.utils.helpers import split_message

TELEGRAM_AVAILABLE = importlib.util.find_spec("telegram") is not None

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import Application, ContextTypes

if TELEGRAM_AVAILABLE:
    from telegram import Update
    from telegram.constants import ChatType
    from telegram.ext import Application, ContextTypes, MessageHandler, filters


MAX_MESSAGE_LEN = 4096  # Telegram's hard limit on a single message


class TelegramConfig(Base):
    """Telegram channel configuration.

    Only ``token`` is strictly required; leave ``allow_from`` empty to deny
    everyone (same convention as the Discord channel).
    """

    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    group_policy: Literal["mention", "open"] = "mention"
    bot_username: str = ""  # auto-filled on start from getMe


class TelegramChannel(BaseChannel):
    """Long-poll Telegram channel. One process, no webhook, no public URL."""

    name = "telegram"
    display_name = "Telegram"

    def __init__(self, config: Any, bus: MessageBus) -> None:
        if isinstance(config, dict):
            config = TelegramConfig(**config)
        super().__init__(config, bus)
        self._app: Application | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._bot_username: str = (config.bot_username or "").lstrip("@").lower()

    async def start(self) -> None:
        """Connect and begin long-polling."""
        if not TELEGRAM_AVAILABLE:
            logger.error("python-telegram-bot not installed; Telegram channel disabled")
            return
        if not self.config.token:
            logger.error("Telegram channel: no token configured")
            return

        app = Application.builder().token(self.config.token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        self._app = app

        await app.initialize()
        try:
            me = await app.bot.get_me()
            self._bot_username = (me.username or "").lower()
            logger.info(
                "Telegram bot connected as @{} (id {})", self._bot_username, me.id
            )
        except Exception as exc:
            logger.error("Telegram getMe failed: {}", exc)
            raise

        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        self._running = True
        logger.info("Telegram channel running (long-poll mode)")

    async def stop(self) -> None:
        """Stop polling and shut down cleanly."""
        self._running = False
        if self._app is None:
            return
        try:
            if self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except Exception as exc:
            logger.warning("Telegram channel shutdown warning: {}", exc)
        finally:
            self._app = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send an outbound message to a Telegram chat."""
        if self._app is None:
            logger.warning("Telegram channel not running; dropping outbound to {}", msg.chat_id)
            return

        chat_id = self._parse_chat_id(msg.chat_id)
        text = msg.content or ""
        # Telegram caps at 4096 chars per message. Split for long replies.
        for chunk in split_message(text, MAX_MESSAGE_LEN):
            await self._app.bot.send_message(chat_id=chat_id, text=chunk)

    @staticmethod
    def _parse_chat_id(raw: str) -> int | str:
        """Telegram chat_ids are integers. Accept strings for robustness."""
        s = (raw or "").strip()
        if not s:
            raise ValueError("empty chat_id")
        try:
            return int(s)
        except ValueError:
            return s  # fall back to username-style (rare)

    # ------------------------------------------------------------------
    # Inbound handler
    # ------------------------------------------------------------------
    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Telegram → bus. Applies group_policy + allowFrom gating."""
        if update.message is None or update.message.from_user is None:
            return
        user = update.message.from_user
        text = update.message.text or ""
        chat = update.effective_chat
        if chat is None:
            return

        is_private = chat.type == ChatType.PRIVATE
        is_mentioned = self._bot_username and f"@{self._bot_username}" in text.lower()

        if not is_private and self.config.group_policy == "mention" and not is_mentioned:
            return  # silent-ignore untargeted group chatter

        # Strip the @mention from the text so the agent doesn't see it as noise.
        if is_mentioned:
            text = text.replace(f"@{self._bot_username}", "", 1).strip()
            # Case-preserving replace on the original if the user typed mixed case
            lower_orig = update.message.text or ""
            if f"@{self._bot_username}" in lower_orig.lower() and f"@{self._bot_username}" not in lower_orig:
                idx = lower_orig.lower().find(f"@{self._bot_username}")
                text = (lower_orig[:idx] + lower_orig[idx + len(self._bot_username) + 1:]).strip()

        if not text:
            return

        await self._handle_message(
            sender_id=str(user.id),
            chat_id=str(chat.id),
            content=text,
            metadata={
                "telegram_username": user.username or "",
                "telegram_first_name": user.first_name or "",
                "is_private": is_private,
            },
        )

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return {
            "enabled": False,
            "token": "",
            "allowFrom": [],
            "groupPolicy": "mention",
        }
