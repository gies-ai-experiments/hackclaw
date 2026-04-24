"""Discord channel implementation using discord.py."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from pydantic import Field

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.command.builtin import build_help_text
from nanobot.config.paths import get_media_dir
from nanobot.config.schema import Base
from nanobot.utils.helpers import safe_filename, split_message

DISCORD_AVAILABLE = importlib.util.find_spec("discord") is not None
if TYPE_CHECKING:
    import discord
    from discord import app_commands
    from discord.abc import Messageable

if DISCORD_AVAILABLE:
    import discord
    from discord import app_commands
    from discord.abc import Messageable

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20MB
MAX_MESSAGE_LEN = 2000  # Discord message character limit
TYPING_INTERVAL_S = 8


class MentorQueueConfig(Base):
    """Configuration for the track-specific mentor queue (/mentorme)."""

    channel_id: str = ""
    """``#mentor-queue`` channel id. Empty disables the ``/mentorme`` command."""

    track_roles: dict[str, str] = Field(default_factory=dict)
    """Map of track slug → mentor role id. The slash-command picker exposes
    these keys to participants; the handler pings the matching role when a
    ticket is posted. E.g. ``{"finance": "1496964848959885465", ...}``."""

    track_voice_ids: dict[str, str] = Field(default_factory=dict)
    """Map of track slug → per-track voice channel id. When an online
    ``/mentorme`` ticket is claimed, the participant's team gets voice
    access to the matching channel — not the shared ``/helpme`` pool.
    Missing entries fall back to the shared pool. Keys should match
    :attr:`track_roles`."""


class HelpQueueConfig(Base):
    """Configuration for the help ticket queue."""

    channel_id: str = ""              # #help-queue channel ID
    mentor_role_id: str = ""          # @TechMentor role ID
    reminder_minutes: int = 10        # Re-ping if unclaimed after N minutes
    office_hours_voice_ids: list[str] = Field(default_factory=list)
    """Voice channel IDs used as the online office-hours room pool.

    Each ticket with ``mode="online"`` is assigned to one of these
    voice channels when a mentor claims it. Permissions on these
    channels must default-deny ``@everyone`` so only the bot (and
    mentors) see them; the bot then grants the current participant a
    ``view_channel + connect`` override for the duration of the session.
    """


class DiscordConfig(Base):
    """Discord channel configuration."""

    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    intents: int = 53763
    # GUILDS | GUILD_MEMBERS | GUILD_INVITES | GUILD_MESSAGES |
    # DIRECT_MESSAGES | MESSAGE_CONTENT.
    # GUILD_MEMBERS is privileged — must be toggled on in the Discord
    # Developer Portal ("Server Members Intent") or on_member_join never
    # fires.
    group_policy: Literal["mention", "open"] = "mention"
    read_receipt_emoji: str = "👀"
    working_emoji: str = "🔧"
    working_emoji_delay: float = 2.0
    help_queue: HelpQueueConfig = Field(default_factory=HelpQueueConfig)
    mentor_queue: MentorQueueConfig = Field(default_factory=MentorQueueConfig)


if DISCORD_AVAILABLE:

    class DiscordBotClient(discord.Client):
        """discord.py client that forwards events to the channel."""

        def __init__(self, channel: DiscordChannel, *, intents: discord.Intents) -> None:
            super().__init__(intents=intents)
            self._channel = channel
            self.tree = app_commands.CommandTree(self)
            from nanobot.channels.invite_tracker import InviteTracker
            self._invite_tracker = InviteTracker()
            self._register_app_commands()

        async def on_ready(self) -> None:
            self._channel._bot_user_id = str(self.user.id) if self.user else None
            logger.info("Discord bot connected as user {}", self._channel._bot_user_id)
            # Set bot nickname to "hackclaw" in each guild
            for guild in self.guilds:
                try:
                    if guild.me and guild.me.nick != "hackclaw":
                        await guild.me.edit(nick="hackclaw")
                        logger.info("Set bot nickname to 'hackclaw' in guild {}", guild.id)
                except Exception as e:
                    logger.warning("Failed to set nickname in guild {}: {}", guild.id, e)
            # Sync commands to each guild first (instant availability)
            for guild in self.guilds:
                try:
                    self.tree.copy_global_to(guild=guild)
                    guild_synced = await self.tree.sync(guild=guild)
                    logger.info("Discord app commands synced to guild {}: {}", guild.id, len(guild_synced))
                except Exception as e:
                    logger.warning("Discord guild sync failed for {}: {}", guild.id, e)
            # Clear global commands to avoid duplicates
            self.tree.clear_commands(guild=None)
            try:
                await self.tree.sync()
                logger.info("Discord global commands cleared")
            except Exception as e:
                logger.warning("Failed to clear global commands: {}", e)

            # Post or refresh the persistent organizer dashboard so
            # /dashboard isn't needed for routine use.
            try:
                from nanobot.helpqueue.views import ensure_dashboard_posted
                cfg = self._channel.config.help_queue
                await ensure_dashboard_posted(
                    self,
                    organizing_channel_id="1493801335831789568",
                    teams_category_id="1493806352139817172",
                    help_queue_channel_id=cfg.channel_id,
                    mentor_role_id=cfg.mentor_role_id,
                )
            except Exception as e:
                logger.warning("Dashboard ensure failed: {}", e)

            # Snapshot current invite uses so on_member_join can diff
            # and detect which invite a new member used.
            for guild in self.guilds:
                await self._invite_tracker.refresh_guild(guild)
            # Background refresh guards against stale cache when invites
            # are created out-of-band (e.g. REST API from the rollout
            # script) and the on_invite_create gateway event is missed
            # or delayed. Runs forever; 15s polls are cheap — one API
            # call per guild.
            if not hasattr(self, "_invite_refresh_task") or self._invite_refresh_task.done():
                self._invite_refresh_task = asyncio.create_task(self._invite_refresh_loop())

        async def _invite_refresh_loop(self) -> None:
            while not self.is_closed():
                try:
                    await asyncio.sleep(15)
                    for guild in self.guilds:
                        await self._invite_tracker.refresh_guild(guild)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning("invite refresh loop error: {}", e)

        async def on_member_join(self, member: discord.Member) -> None:
            from nanobot.channels.invite_tracker import on_member_join_assign_role
            await on_member_join_assign_role(self._invite_tracker, member)

        async def on_invite_create(self, invite: discord.Invite) -> None:
            logger.info(
                "on_invite_create fired: code={} guild={} uses={} max_uses={}",
                invite.code,
                invite.guild.id if invite.guild else None,
                invite.uses,
                invite.max_uses,
            )
            if invite.guild is not None:
                await self._invite_tracker.refresh_guild(invite.guild)

        async def on_invite_delete(self, invite: discord.Invite) -> None:
            logger.info(
                "on_invite_delete fired: code={} guild={}",
                invite.code,
                invite.guild.id if invite.guild else None,
            )
            if invite.guild is not None:
                await self._invite_tracker.refresh_guild(invite.guild)

        async def on_message(self, message: discord.Message) -> None:
            await self._channel._handle_discord_message(message)

        async def setup_hook(self) -> None:
            from nanobot.helpqueue.views import ClaimView, DashboardView
            self.add_view(ClaimView(""))
            # Register the persistent dashboard view so its buttons keep
            # working across bot restarts. team_channels=[] → the select
            # re-populates on the next refresh / post.
            self.add_view(DashboardView(
                team_channels=[],
                teams_category_id="1493806352139817172",
                help_queue_channel_id="",
                mentor_role_id="",
            ))

        async def on_interaction(self, interaction: discord.Interaction) -> None:
            if interaction.type != discord.InteractionType.component:
                return
            custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
            if not custom_id.startswith("helpqueue:"):
                return
            ticket_id = None
            if interaction.message and interaction.message.embeds:
                title = interaction.message.embeds[0].title or ""
                ticket_id = title.split(" — ")[0].strip() if " — " in title else None
            if not ticket_id:
                await interaction.response.send_message("Could not identify the ticket.", ephemeral=True)
                return
            from nanobot.helpqueue.handler import handle_claim, handle_resolve_button, handle_unclaim, get_store
            if custom_id == "helpqueue:claim":
                await handle_claim(interaction, ticket_id)
            elif custom_id == "helpqueue:unclaim":
                await handle_unclaim(interaction, ticket_id)
            elif custom_id == "helpqueue:resolve":
                # /mentorme tickets live in #mentor-queue; /helpme tickets in #help-queue.
                # The ticket itself carries its queue channel id when set.
                cfg_help = self._channel.config.help_queue
                t = get_store().get(ticket_id)
                channel_id = (
                    str(t.queue_channel_id) if (t and t.queue_channel_id) else cfg_help.channel_id
                )
                await handle_resolve_button(interaction, ticket_id, channel_id)

        async def _reply_ephemeral(self, interaction: discord.Interaction, text: str) -> bool:
            """Send an ephemeral interaction response and report success."""
            try:
                await interaction.response.send_message(text, ephemeral=True)
                return True
            except Exception as e:
                logger.warning("Discord interaction response failed: {}", e)
                return False

        async def _forward_slash_command(
            self,
            interaction: discord.Interaction,
            command_text: str,
        ) -> None:
            sender_id = str(interaction.user.id)
            channel_id = interaction.channel_id

            if channel_id is None:
                logger.warning("Discord slash command missing channel_id: {}", command_text)
                return

            if not self._channel.is_allowed(sender_id):
                await self._reply_ephemeral(interaction, "You are not allowed to use this bot.")
                return

            await self._reply_ephemeral(interaction, f"Processing {command_text}...")

            await self._channel._handle_message(
                sender_id=sender_id,
                chat_id=str(channel_id),
                content=command_text,
                metadata={
                    "interaction_id": str(interaction.id),
                    "guild_id": str(interaction.guild_id) if interaction.guild_id else None,
                    "is_slash_command": True,
                },
            )

        def _register_app_commands(self) -> None:
            @self.tree.command(name="helpme", description="Request help from a technical mentor")
            @app_commands.describe(
                problem="What do you need help with?",
                mode="How would you like the help? (in-person at your table, or online in a voice channel)",
                location="If in-person: where you're sitting (e.g., BIF 2007). Ignored for online.",
            )
            @app_commands.choices(
                mode=[
                    app_commands.Choice(name="In-person (mentor comes to your table)", value="in_person"),
                    app_commands.Choice(name="Online (voice channel office hours)", value="online"),
                ],
            )
            async def helpme_command(
                interaction: discord.Interaction,
                problem: str,
                mode: app_commands.Choice[str],
                location: str = "",
            ) -> None:
                from nanobot.helpqueue.handler import helpme_instant
                cfg = self._channel.config.help_queue
                await helpme_instant(
                    interaction,
                    location=location,
                    problem=problem,
                    mode=mode.value,
                    help_queue_channel_id=cfg.channel_id,
                    mentor_role_id=cfg.mentor_role_id,
                    office_hours_voice_ids=cfg.office_hours_voice_ids,
                )

            @self.tree.command(name="resolved", description="Mark your help request as resolved")
            async def resolved_command(interaction: discord.Interaction) -> None:
                from nanobot.helpqueue.handler import handle_resolve_command
                cfg = self._channel.config.help_queue
                await handle_resolve_command(interaction, help_queue_channel_id=cfg.channel_id)

            # /mentorme — track-specific mentor request (finance, HR, etc.).
            # Registered only when mentor_queue.channel_id is configured.
            _mentor_cfg = self._channel.config.mentor_queue
            if _mentor_cfg.channel_id and _mentor_cfg.track_roles:
                _track_choices = [
                    app_commands.Choice(name=slug, value=slug)
                    for slug in _mentor_cfg.track_roles.keys()
                ]

                @self.tree.command(
                    name="mentorme",
                    description="Request a track-specific mentor (finance, HR, business-tech, etc.)",
                )
                @app_commands.describe(
                    track="Which mentor track best fits your question?",
                    problem="What do you need help with?",
                    mode="In-person (mentor comes to you) or online (also gets a voice channel)",
                    location="If in-person: where you're sitting (e.g., BIF 2007). Ignored for online.",
                )
                @app_commands.choices(
                    track=_track_choices,
                    mode=[
                        app_commands.Choice(name="In-person (mentor comes to your table)", value="in_person"),
                        app_commands.Choice(name="Online (voice channel + team text channel)", value="online"),
                    ],
                )
                async def mentorme_command(
                    interaction: discord.Interaction,
                    track: app_commands.Choice[str],
                    problem: str,
                    mode: app_commands.Choice[str],
                    location: str = "",
                ) -> None:
                    from nanobot.helpqueue.handler import mentorme_instant
                    mcfg = self._channel.config.mentor_queue
                    role_id = mcfg.track_roles.get(track.value, "")
                    if not role_id:
                        await interaction.response.send_message(
                            f"No mentor role is mapped for track `{track.value}` — "
                            "ask an organizer.",
                            ephemeral=True,
                        )
                        return
                    # If the track has a dedicated voice channel configured,
                    # pin the online session to that room so each track is
                    # isolated. Without one, fall back to the shared /helpme
                    # pool so the feature still works end-to-end.
                    track_voice_id = mcfg.track_voice_ids.get(track.value) or None
                    await mentorme_instant(
                        interaction,
                        track=track.value,
                        problem=problem,
                        mode=mode.value,
                        location=location,
                        mentor_queue_channel_id=mcfg.channel_id,
                        track_role_id=role_id,
                        track_voice_id=track_voice_id,
                        office_hours_voice_ids=self._channel.config.help_queue.office_hours_voice_ids,
                    )

            @self.tree.command(
                name="introduce",
                description="Meet hackclaw — what the bot can do and how to use it",
            )
            async def introduce_command(interaction: discord.Interaction) -> None:
                TEAMS_CATEGORY_ID = "1493806352139817172"
                parent_id = getattr(interaction.channel, "category_id", None)
                if parent_id is None or str(parent_id) != TEAMS_CATEGORY_ID:
                    await interaction.response.send_message(
                        "Run `/introduce` from your **team's text channel** "
                        "(under the Teams category). The intro is scoped to "
                        "individual team channels so it doesn't spam the "
                        "general chat.",
                        ephemeral=True,
                    )
                    return
                team = getattr(interaction.channel, "name", "your team")
                team_pretty = team.replace("-", " ").title() if team else "your team"
                intro = (
                    f"Hi **{team_pretty}** — I'm **hackclaw**, the AI assistant for the "
                    "Gies AI for Impact Challenge. Quick tour of what I can do in this channel:\n\n"
                    "**Ask me anything about the event.** Mention me (`@hackclaw …`) and "
                    "I'll pull from the event knowledge base — schedule, rooms, tracks, "
                    "judging, Copilot Studio reference, prior teams' help-ticket resolutions. "
                    "Examples:\n"
                    "• `@hackclaw what time is check-in?`\n"
                    "• `@hackclaw where is the opening ceremony?`\n"
                    "• `@hackclaw what is Copilot Studio?`\n"
                    "• `@hackclaw how do I connect Excel to my agent?`\n\n"
                    "**Slash commands available to your team:**\n"
                    "• `/introduce` — this message.\n"
                    "• `/helpme problem:<what's wrong> mode:<in-person|online>` — "
                    "creates a help ticket. In-person routes a mentor to your table; "
                    "online puts your whole team into a private voice channel with "
                    "a mentor once claimed.\n"
                    "• `/resolved` — close an open ticket (claimed mentor only).\n\n"
                    "**Things to know:**\n"
                    "• Submissions are due **1:00 PM on Apr 24**. Apply here: "
                    "<https://forms.gle/Az8PGE1u8rwwkFWy8>.\n"
                    "• Event is **BIF**, April 23–24. Check-in starts **4:30 PM Thursday**, BIF West.\n"
                    "• Mentor office hours + workshops are posted in the organizing channel; "
                    "ask me for specific times.\n"
                    "• If I give a wrong answer, correct me in the thread — I'll use that "
                    "context for the rest of your team's conversation."
                )
                await interaction.response.send_message(intro)

            @self.tree.command(name="dashboard", description="Show the organizer dashboard")
            async def dashboard_command(interaction: discord.Interaction) -> None:
                ORGANIZING_CHANNEL_ID = "1493801335831789568"
                TEAMS_CATEGORY_ID = "1493806352139817172"

                if str(interaction.channel_id) != ORGANIZING_CHANNEL_ID:
                    await interaction.response.send_message(
                        "This command can only be used in the organizing team channel.",
                        ephemeral=True,
                    )
                    return

                from nanobot.helpqueue.handler import get_store
                from nanobot.helpqueue.views import DashboardView, build_dashboard_embed

                store = get_store()
                open_count = sum(1 for t in store._tickets.values() if t.status == "open")
                claimed_count = sum(1 for t in store._tickets.values() if t.status == "claimed")
                resolved_count = sum(1 for t in store._tickets.values() if t.status == "resolved")

                # Get team channels from category
                team_channels = []
                if interaction.guild:
                    category = interaction.guild.get_channel(int(TEAMS_CATEGORY_ID))
                    if category:
                        team_channels = [
                            (str(ch.id), ch.name)
                            for ch in category.channels
                            if isinstance(ch, discord.TextChannel)
                        ]

                cfg = self._channel.config.help_queue
                embed = build_dashboard_embed(open_count, claimed_count, resolved_count, len(team_channels))
                view = DashboardView(
                    team_channels=team_channels,
                    teams_category_id=TEAMS_CATEGORY_ID,
                    help_queue_channel_id=cfg.channel_id,
                    mentor_role_id=cfg.mentor_role_id,
                )
                await interaction.response.send_message(embed=embed, view=view)

            @self.tree.error
            async def on_app_command_error(
                interaction: discord.Interaction,
                error: app_commands.AppCommandError,
            ) -> None:
                command_name = interaction.command.qualified_name if interaction.command else "?"
                logger.warning(
                    "Discord app command failed user={} channel={} cmd={} error={}",
                    interaction.user.id,
                    interaction.channel_id,
                    command_name,
                    error,
                )

        async def send_outbound(self, msg: OutboundMessage) -> None:
            """Send a nanobot outbound message using Discord transport rules."""
            channel_id = int(msg.chat_id)

            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                except Exception as e:
                    logger.warning("Discord channel {} unavailable: {}", msg.chat_id, e)
                    return

            reference, mention_settings = self._build_reply_context(channel, msg.reply_to)
            sent_media = False
            failed_media: list[str] = []

            for index, media_path in enumerate(msg.media or []):
                if await self._send_file(
                    channel,
                    media_path,
                    reference=reference if index == 0 else None,
                    mention_settings=mention_settings,
                ):
                    sent_media = True
                else:
                    failed_media.append(Path(media_path).name)

            for index, chunk in enumerate(self._build_chunks(msg.content or "", failed_media, sent_media)):
                kwargs: dict[str, Any] = {"content": chunk}
                if index == 0 and reference is not None and not sent_media:
                    kwargs["reference"] = reference
                    kwargs["allowed_mentions"] = mention_settings
                await channel.send(**kwargs)

        async def _send_file(
            self,
            channel: Messageable,
            file_path: str,
            *,
            reference: discord.PartialMessage | None,
            mention_settings: discord.AllowedMentions,
        ) -> bool:
            """Send a file attachment via discord.py."""
            path = Path(file_path)
            if not path.is_file():
                logger.warning("Discord file not found, skipping: {}", file_path)
                return False

            if path.stat().st_size > MAX_ATTACHMENT_BYTES:
                logger.warning("Discord file too large (>20MB), skipping: {}", path.name)
                return False

            try:
                kwargs: dict[str, Any] = {"file": discord.File(path)}
                if reference is not None:
                    kwargs["reference"] = reference
                    kwargs["allowed_mentions"] = mention_settings
                await channel.send(**kwargs)
                logger.info("Discord file sent: {}", path.name)
                return True
            except Exception as e:
                logger.error("Error sending Discord file {}: {}", path.name, e)
                return False

        @staticmethod
        def _build_chunks(content: str, failed_media: list[str], sent_media: bool) -> list[str]:
            """Build outbound text chunks, including attachment-failure fallback text."""
            chunks = split_message(content, MAX_MESSAGE_LEN)
            if chunks or not failed_media or sent_media:
                return chunks
            fallback = "\n".join(f"[attachment: {name} - send failed]" for name in failed_media)
            return split_message(fallback, MAX_MESSAGE_LEN)

        @staticmethod
        def _build_reply_context(
            channel: Messageable,
            reply_to: str | None,
        ) -> tuple[discord.PartialMessage | None, discord.AllowedMentions]:
            """Build reply context for outbound messages."""
            mention_settings = discord.AllowedMentions(replied_user=False)
            if not reply_to:
                return None, mention_settings
            try:
                message_id = int(reply_to)
            except ValueError:
                logger.warning("Invalid Discord reply target: {}", reply_to)
                return None, mention_settings

            return channel.get_partial_message(message_id), mention_settings


class DiscordChannel(BaseChannel):
    """Discord channel using discord.py."""

    name = "discord"
    display_name = "Discord"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return DiscordConfig().model_dump(by_alias=True)

    @staticmethod
    def _channel_key(channel_or_id: Any) -> str:
        """Normalize channel-like objects and ids to a stable string key."""
        channel_id = getattr(channel_or_id, "id", channel_or_id)
        return str(channel_id)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = DiscordConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: DiscordConfig = config
        self._client: DiscordBotClient | None = None
        self._typing_tasks: dict[str, asyncio.Task[None]] = {}
        self._bot_user_id: str | None = None
        self._pending_reactions: dict[str, Any] = {}  # chat_id -> message object
        self._working_emoji_tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        """Start the Discord client."""
        if not DISCORD_AVAILABLE:
            logger.error("discord.py not installed. Run: pip install nanobot-ai[discord]")
            return

        if not self.config.token:
            logger.error("Discord bot token not configured")
            return

        try:
            intents = discord.Intents.none()
            intents.value = self.config.intents
            self._client = DiscordBotClient(self, intents=intents)
        except Exception as e:
            logger.error("Failed to initialize Discord client: {}", e)
            self._client = None
            self._running = False
            return

        self._running = True
        logger.info("Starting Discord client via discord.py...")

        try:
            await self._client.start(self.config.token)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Discord client startup failed: {}", e)
        finally:
            self._running = False
            await self._reset_runtime_state(close_client=True)

    async def stop(self) -> None:
        """Stop the Discord channel."""
        self._running = False
        await self._reset_runtime_state(close_client=True)

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Discord using discord.py."""
        client = self._client
        if client is None or not client.is_ready():
            logger.warning("Discord client not ready; dropping outbound message")
            return

        is_progress = bool((msg.metadata or {}).get("_progress"))

        try:
            await client.send_outbound(msg)
        except Exception as e:
            logger.error("Error sending Discord message: {}", e)
        finally:
            if not is_progress:
                await self._stop_typing(msg.chat_id)
                await self._clear_reactions(msg.chat_id)

    async def _handle_discord_message(self, message: discord.Message) -> None:
        """Handle incoming Discord messages from discord.py."""
        if message.author.bot:
            return

        sender_id = str(message.author.id)
        channel_id = self._channel_key(message.channel)
        content = message.content or ""

        if not self._should_accept_inbound(message, sender_id, content):
            return

        media_paths, attachment_markers = await self._download_attachments(message.attachments)
        full_content = self._compose_inbound_content(content, attachment_markers)
        metadata = self._build_inbound_metadata(message)

        # Copilot Studio channel: replace system prompt knowledge with copilot textbook only
        COPILOT_CHANNEL_ID = "1493684284332965978"
        if channel_id == COPILOT_CHANNEL_ID:
            copilot_path = Path(__file__).resolve().parent.parent.parent / "brain" / "copilot-studio.md"
            try:
                copilot_content = copilot_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                copilot_content = ""
                logger.warning("Copilot textbook not found at {}", copilot_path)
            if copilot_content:
                metadata["_knowledge_override"] = (
                    "# Copilot Studio Expert\n\n"
                    "You are **hackclaw**, the Copilot Studio expert for the "
                    "Gies AI for Impact Challenge hackathon. "
                    "When asked to introduce yourself, say who you are and that you help "
                    "hackathon participants with Microsoft Copilot Studio questions.\n\n"
                    "Answer questions using the textbook below. "
                    "Do NOT use general knowledge or other sources. "
                    "If the question is not about Microsoft Copilot Studio "
                    "and is not a greeting or self-introduction, politely say: "
                    "'This channel is for Copilot Studio questions only. "
                    "For other questions, ask me in your team channel or the general channel.'\n\n"
                    "Keep answers concise and practical for hackathon participants.\n\n"
                    "---\n\n"
                    f"# Copilot Studio Textbook\n\n{copilot_content}"
                )

        await self._start_typing(message.channel)

        # Add read receipt reaction immediately, working emoji after delay
        channel_id = self._channel_key(message.channel)
        try:
            await message.add_reaction(self.config.read_receipt_emoji)
            self._pending_reactions[channel_id] = message
        except Exception as e:
            logger.debug("Failed to add read receipt reaction: {}", e)

        # Delayed working indicator (cosmetic — not tied to subagent lifecycle)
        async def _delayed_working_emoji() -> None:
            await asyncio.sleep(self.config.working_emoji_delay)
            try:
                await message.add_reaction(self.config.working_emoji)
            except Exception:
                pass

        self._working_emoji_tasks[channel_id] = asyncio.create_task(_delayed_working_emoji())

        try:
            await self._handle_message(
                sender_id=sender_id,
                chat_id=channel_id,
                content=full_content,
                media=media_paths,
                metadata=metadata,
            )
        except Exception:
            await self._clear_reactions(channel_id)
            await self._stop_typing(channel_id)
            raise

    async def _on_message(self, message: discord.Message) -> None:
        """Backward-compatible alias for legacy tests/callers."""
        await self._handle_discord_message(message)

    def _should_accept_inbound(
        self,
        message: discord.Message,
        sender_id: str,
        content: str,
    ) -> bool:
        """Check if inbound Discord message should be processed."""
        if not self.is_allowed(sender_id):
            return False
        if message.guild is not None and not self._should_respond_in_group(message, content):
            return False
        return True

    async def _download_attachments(
        self,
        attachments: list[discord.Attachment],
    ) -> tuple[list[str], list[str]]:
        """Download supported attachments and return paths + display markers."""
        media_paths: list[str] = []
        markers: list[str] = []
        media_dir = get_media_dir("discord")

        for attachment in attachments:
            filename = attachment.filename or "attachment"
            if attachment.size and attachment.size > MAX_ATTACHMENT_BYTES:
                markers.append(f"[attachment: {filename} - too large]")
                continue
            try:
                media_dir.mkdir(parents=True, exist_ok=True)
                safe_name = safe_filename(filename)
                file_path = media_dir / f"{attachment.id}_{safe_name}"
                await attachment.save(file_path)
                media_paths.append(str(file_path))
                markers.append(f"[attachment: {file_path.name}]")
            except Exception as e:
                logger.warning("Failed to download Discord attachment: {}", e)
                markers.append(f"[attachment: {filename} - download failed]")

        return media_paths, markers

    @staticmethod
    def _compose_inbound_content(content: str, attachment_markers: list[str]) -> str:
        """Combine message text with attachment markers."""
        content_parts = [content] if content else []
        content_parts.extend(attachment_markers)
        return "\n".join(part for part in content_parts if part) or "[empty message]"

    @staticmethod
    def _build_inbound_metadata(message: discord.Message) -> dict[str, str | None]:
        """Build metadata for inbound Discord messages."""
        reply_to = str(message.reference.message_id) if message.reference and message.reference.message_id else None
        return {
            "message_id": str(message.id),
            "guild_id": str(message.guild.id) if message.guild else None,
            "reply_to": reply_to,
        }

    def _should_respond_in_group(self, message: discord.Message, content: str) -> bool:
        """Check if the bot should respond in a guild channel based on policy."""
        # Always respond in the Copilot Studio channel (no @mention needed)
        COPILOT_CHANNEL_ID = "1493684284332965978"
        if str(message.channel.id) == COPILOT_CHANNEL_ID:
            return True

        if self.config.group_policy == "open":
            return True

        if self.config.group_policy == "mention":
            bot_user_id = self._bot_user_id
            if bot_user_id is None:
                logger.debug("Discord message in {} ignored (bot identity unavailable)", message.channel.id)
                return False

            # User mention via autocomplete (pings the bot *user*).
            if any(str(user.id) == bot_user_id for user in message.mentions):
                return True
            if f"<@{bot_user_id}>" in content or f"<@!{bot_user_id}>" in content:
                return True

            # Role mention — Discord's autocomplete sometimes offers the
            # bot's managed integration role (same name as the bot) and
            # users pick that instead of the bot user, producing
            # `<@&roleid>` syntax. Treat any managed role tied to the
            # bot's own member as a valid mention.
            bot_member = message.guild.me if message.guild else None
            if bot_member is not None and message.role_mentions:
                bot_role_ids = {str(r.id) for r in getattr(bot_member, "roles", [])}
                if any(str(r.id) in bot_role_ids for r in message.role_mentions):
                    return True

            logger.debug("Discord message in {} ignored (bot not mentioned)", message.channel.id)
            return False

        return True

    async def _start_typing(self, channel: Messageable) -> None:
        """Start periodic typing indicator for a channel."""
        channel_id = self._channel_key(channel)
        await self._stop_typing(channel_id)

        async def typing_loop() -> None:
            while self._running:
                try:
                    async with channel.typing():
                        await asyncio.sleep(TYPING_INTERVAL_S)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.debug("Discord typing indicator failed for {}: {}", channel_id, e)
                    return

        self._typing_tasks[channel_id] = asyncio.create_task(typing_loop())

    async def _stop_typing(self, channel_id: str) -> None:
        """Stop typing indicator for a channel."""
        task = self._typing_tasks.pop(self._channel_key(channel_id), None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


    async def _clear_reactions(self, chat_id: str) -> None:
        """Remove all pending reactions after bot replies."""
        # Cancel delayed working emoji if it hasn't fired yet
        task = self._working_emoji_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

        msg_obj = self._pending_reactions.pop(chat_id, None)
        if msg_obj is None:
            return
        bot_user = self._client.user if self._client else None
        for emoji in (self.config.read_receipt_emoji, self.config.working_emoji):
            try:
                await msg_obj.remove_reaction(emoji, bot_user)
            except Exception:
                pass

    async def _cancel_all_typing(self) -> None:
        """Stop all typing tasks."""
        channel_ids = list(self._typing_tasks)
        for channel_id in channel_ids:
            await self._stop_typing(channel_id)

    async def _reset_runtime_state(self, close_client: bool) -> None:
        """Reset client and typing state."""
        await self._cancel_all_typing()
        if close_client and self._client is not None and not self._client.is_closed():
            try:
                await self._client.close()
            except Exception as e:
                logger.warning("Discord client close failed: {}", e)
        self._client = None
        self._bot_user_id = None
