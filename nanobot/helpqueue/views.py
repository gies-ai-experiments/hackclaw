"""Discord UI components for the help ticket queue."""

from __future__ import annotations

import discord
from loguru import logger

from nanobot.helpqueue.ticket import HelpTicket

STATUS_COLOURS = {
    "open": discord.Colour(0xFF9900),      # orange
    "claimed": discord.Colour(0x3498DB),   # blue
    "resolved": discord.Colour(0x2ECC71),  # green
}

STATUS_LABELS = {
    "open": "Waiting for mentor",
    "claimed": "Mentor on the way",
    "resolved": "Resolved",
}


def build_ticket_embed(
    ticket: HelpTicket,
    *,
    queue_position: int | None = None,
) -> discord.Embed:
    """Build a Discord embed for a help ticket.

    *queue_position* (1-based) is shown for online tickets that are still
    waiting in the queue. Ignored for in-person and for claimed/resolved.
    """
    mode_badge = "ONLINE" if ticket.mode == "online" else "IN-PERSON"
    embed = discord.Embed(
        title=f"{ticket.id} — {ticket.team_name}  ·  {mode_badge}",
        colour=STATUS_COLOURS.get(ticket.status, discord.Colour.greyple()),
    )
    if ticket.mode == "in_person":
        embed.add_field(name="Location", value=ticket.location, inline=True)
    embed.add_field(name="Status", value=STATUS_LABELS.get(ticket.status, ticket.status), inline=True)

    if (
        ticket.mode == "online"
        and ticket.status == "open"
        and queue_position is not None
        and queue_position > 0
    ):
        embed.add_field(name="Queue position", value=f"#{queue_position}", inline=True)

    embed.add_field(name="Description", value=ticket.description, inline=False)

    if ticket.claimed_by_name:
        embed.add_field(name="Claimed by", value=ticket.claimed_by_name, inline=True)

    if ticket.mode == "online" and ticket.online_room_id and ticket.status == "claimed":
        embed.add_field(name="Voice room", value=f"<#{ticket.online_room_id}>", inline=True)

    if ticket.status == "resolved" and ticket.created_at and ticket.resolved_at:
        delta = ticket.resolved_at - ticket.created_at
        minutes = int(delta.total_seconds() / 60)
        embed.add_field(name="Resolved in", value=f"{minutes} min", inline=True)

    embed.timestamp = ticket.created_at
    return embed


class ClaimView(discord.ui.View):
    """Persistent view with Claim/Unclaim buttons for a help ticket."""

    def __init__(self, ticket_id: str) -> None:
        super().__init__(timeout=None)
        self.ticket_id = ticket_id

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, custom_id="helpqueue:claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass  # Handled by DiscordChannel interaction handler

    @discord.ui.button(label="Unclaim", style=discord.ButtonStyle.secondary, custom_id="helpqueue:unclaim", disabled=True)
    async def unclaim_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass  # Handled by DiscordChannel interaction handler

    @discord.ui.button(label="Resolve", style=discord.ButtonStyle.success, custom_id="helpqueue:resolve", disabled=True)
    async def resolve_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass  # Handled by DiscordChannel interaction handler


class ResolveModal(discord.ui.Modal, title="Resolve Ticket"):
    """Modal that captures how the mentor solved the issue."""

    solution = discord.ui.TextInput(
        label="How did you solve this?",
        style=discord.TextStyle.paragraph,
        placeholder="Describe the fix so future teams can try it themselves...",
        required=True,
        max_length=1000,
    )

    def __init__(self, ticket_id: str, help_queue_channel_id: str) -> None:
        super().__init__()
        self.ticket_id = ticket_id
        self.help_queue_channel_id = help_queue_channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from nanobot.helpqueue.handler import handle_resolve_with_solution
        await handle_resolve_with_solution(
            interaction,
            self.ticket_id,
            self.solution.value,
            self.help_queue_channel_id,
        )


class SuggestionView(discord.ui.View):
    """Buttons shown when a similar past solution is found."""

    def __init__(
        self,
        location: str,
        problem: str,
        help_queue_channel_id: str,
        mentor_role_id: str,
        channel_id: int,
        team_name: str,
    ) -> None:
        super().__init__(timeout=120)
        self.location = location
        self.problem = problem
        self.help_queue_channel_id = help_queue_channel_id
        self.mentor_role_id = mentor_role_id
        self.channel_id = channel_id
        self.team_name = team_name

    @discord.ui.button(label="Yes, still stuck", style=discord.ButtonStyle.danger)
    async def still_stuck(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from nanobot.helpqueue.handler import create_ticket_from_suggestion
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await create_ticket_from_suggestion(
            interaction, self.location, self.problem,
            self.help_queue_channel_id, self.mentor_role_id,
            self.channel_id, self.team_name,
        )

    @discord.ui.button(label="No, let me try", style=discord.ButtonStyle.primary)
    async def let_me_try(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(view=None)
        follow_up = FollowUpView(
            self.location, self.problem,
            self.help_queue_channel_id, self.mentor_role_id,
            self.channel_id, self.team_name,
        )
        await interaction.followup.send(
            "Take your time! Let me know when you're done:",
            view=follow_up,
        )

    async def on_timeout(self) -> None:
        pass


class FollowUpView(discord.ui.View):
    """Follow-up buttons after user tries a suggestion."""

    def __init__(
        self,
        location: str,
        problem: str,
        help_queue_channel_id: str,
        mentor_role_id: str,
        channel_id: int,
        team_name: str,
    ) -> None:
        super().__init__(timeout=300)
        self.location = location
        self.problem = problem
        self.help_queue_channel_id = help_queue_channel_id
        self.mentor_role_id = mentor_role_id
        self.channel_id = channel_id
        self.team_name = team_name

    @discord.ui.button(label="Yes, solved!", style=discord.ButtonStyle.success)
    async def solved(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Glad that helped! Happy hacking!", view=None)

    @discord.ui.button(label="No, still need help", style=discord.ButtonStyle.danger)
    async def still_need_help(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from nanobot.helpqueue.handler import create_ticket_from_suggestion
        await interaction.response.defer()
        await interaction.message.edit(content="No worries — getting a mentor for you now!", view=None)
        await create_ticket_from_suggestion(
            interaction, self.location, self.problem,
            self.help_queue_channel_id, self.mentor_role_id,
            self.channel_id, self.team_name,
        )

    async def on_timeout(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Organizer dashboard
# ---------------------------------------------------------------------------


def build_dashboard_embed(
    open_count: int,
    claimed_count: int,
    resolved_count: int,
    team_count: int,
) -> discord.Embed:
    """Build the organizer dashboard embed.

    The footer text includes :data:`DASHBOARD_MARKER` so
    :func:`ensure_dashboard_posted` can identify and edit-in-place any
    existing dashboard message across bot restarts instead of spawning
    duplicates.
    """
    embed = discord.Embed(
        title="Hackclaw Organizer Dashboard",
        colour=discord.Colour(0x9B59B6),
    )
    embed.add_field(
        name="Help Queue",
        value=(
            f"Open: **{open_count}**\n"
            f"Claimed: **{claimed_count}**\n"
            f"Resolved: **{resolved_count}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Team Channels",
        value=f"**{team_count}** teams found",
        inline=True,
    )
    embed.set_footer(text=DASHBOARD_MARKER)
    return embed


async def ensure_dashboard_posted(
    client: discord.Client,
    *,
    organizing_channel_id: str,
    teams_category_id: str,
    help_queue_channel_id: str,
    mentor_role_id: str,
) -> None:
    """Make sure a live dashboard message exists in the organizing channel.

    Scans the last 50 messages for one whose embed footer contains
    :data:`DASHBOARD_MARKER`. If found: edit it in place with fresh counts
    and team list (so a bot restart recovers the existing message without
    leaving a stale one). If not found: post a new one.

    Safe to call idempotently on every ``on_ready`` — it never posts a
    second dashboard when one already exists.
    """
    try:
        channel = client.get_channel(int(organizing_channel_id))
        if channel is None:
            channel = await client.fetch_channel(int(organizing_channel_id))
    except Exception as exc:
        logger.warning("Dashboard: can't open organizing channel {}: {}",
                       organizing_channel_id, exc)
        return

    from nanobot.helpqueue.handler import get_store
    store = get_store()
    open_count = sum(1 for t in store._tickets.values() if t.status == "open")
    claimed_count = sum(1 for t in store._tickets.values() if t.status == "claimed")
    resolved_count = sum(1 for t in store._tickets.values() if t.status == "resolved")

    guild = getattr(channel, "guild", None)
    team_channels: list[tuple[str, str]] = []
    if guild is not None:
        try:
            category = guild.get_channel(int(teams_category_id))
            if category is not None:
                team_channels = [
                    (str(ch.id), ch.name)
                    for ch in category.channels
                    if isinstance(ch, discord.TextChannel)
                ]
        except Exception as exc:
            logger.warning("Dashboard: can't enumerate team channels: {}", exc)

    embed = build_dashboard_embed(open_count, claimed_count, resolved_count, len(team_channels))
    view = DashboardView(
        team_channels=team_channels,
        teams_category_id=teams_category_id,
        help_queue_channel_id=help_queue_channel_id,
        mentor_role_id=mentor_role_id,
    )

    # Look for an existing dashboard to edit in place.
    me = getattr(client, "user", None)
    me_id = me.id if me is not None else None
    existing = None
    try:
        async for msg in channel.history(limit=50):
            if me_id is not None and msg.author.id != me_id:
                continue
            if not msg.embeds:
                continue
            footer = msg.embeds[0].footer.text if msg.embeds[0].footer else ""
            if footer and DASHBOARD_MARKER in footer:
                existing = msg
                break
    except Exception as exc:
        logger.warning("Dashboard: history scan failed: {}", exc)

    try:
        if existing is not None:
            await existing.edit(embed=embed, view=view)
            logger.info("Dashboard: updated existing message {}", existing.id)
        else:
            sent = await channel.send(embed=embed, view=view)
            logger.info("Dashboard: posted new message {}", sent.id)
    except Exception as exc:
        logger.exception("Dashboard: post/update failed: {}", exc)


# Module-level tracker for "which channel did which user pick from which
# dashboard message" — keyed by (message_id, user_id) so multiple organizers
# can use the same persistent dashboard concurrently without stepping on
# each other's selections, and so a restart wipes stale state cleanly.
_dashboard_selections: dict[tuple[int, int], str] = {}

# Marker used to identify the persistent dashboard message across bot
# restarts when scanning the organizing channel's recent history. Bumping
# the version string forces a fresh dashboard to be posted (old ones
# stop matching on startup + get ignored).
DASHBOARD_MARKER = "hackclaw-dashboard-v1"


class DashboardView(discord.ui.View):
    """Organizer dashboard with team channel join/leave and ticket stats.

    Persistent view (``timeout=None``) — the dashboard is posted once to
    the organizing channel at bot startup and stays there for the life
    of the event. Buttons keep working across bot restarts because
    they're registered with stable ``custom_id``s via ``add_view()`` in
    the gateway's ``setup_hook``.

    Per-user selection state lives in the module-level
    :data:`_dashboard_selections` dict rather than on the view instance,
    since the view instance is shared across all interactions on the
    single persistent message.
    """

    def __init__(
        self,
        team_channels: list[tuple[str, str]],
        teams_category_id: str,
        help_queue_channel_id: str,
        mentor_role_id: str,
    ) -> None:
        super().__init__(timeout=None)
        self.teams_category_id = teams_category_id
        self.help_queue_channel_id = help_queue_channel_id
        self.mentor_role_id = mentor_role_id

        if team_channels:
            options = [
                discord.SelectOption(label=name, value=cid)
                for cid, name in team_channels[:25]
            ]
        else:
            options = [discord.SelectOption(label="No teams found", value="none")]
        self.channel_select.options = options

    @discord.ui.select(
        custom_id="dashboard:v2:channel_select",
        placeholder="Select a team channel...",
    )
    async def channel_select(
        self, interaction: discord.Interaction, select: discord.ui.Select,
    ) -> None:
        if select.values and interaction.message:
            _dashboard_selections[(interaction.message.id, interaction.user.id)] = select.values[0]
            logger.info(
                "Dashboard select: msg={} user={} -> {!r}",
                interaction.message.id, interaction.user.id, select.values[0],
            )
        await interaction.response.defer()

    def _get_selection(self, interaction: discord.Interaction) -> str | None:
        if interaction.message is None:
            return None
        return _dashboard_selections.get((interaction.message.id, interaction.user.id))

    @discord.ui.button(
        label="Join", style=discord.ButtonStyle.success, row=2,
        custom_id="dashboard:v2:join",
    )
    async def join_button(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        sel = self._get_selection(interaction)
        if not sel or sel == "none":
            await interaction.response.send_message(
                "Pick a team channel from the dropdown above first.",
                ephemeral=True,
            )
            return
        try:
            channel = interaction.client.get_channel(int(sel))
            if channel is None:
                channel = await interaction.client.fetch_channel(int(sel))
            if channel is None:
                raise LookupError(f"channel id {sel} not found in this guild")
            member = interaction.user
            if not isinstance(member, discord.Member) and interaction.guild:
                member = interaction.guild.get_member(interaction.user.id) or (
                    await interaction.guild.fetch_member(interaction.user.id)
                )
            await channel.set_permissions(
                member,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                reason="Organizer joined via dashboard",
            )
            await interaction.response.send_message(
                f"Joined <#{sel}>.", ephemeral=True,
            )
            logger.info(
                "Dashboard join ok user={} -> channel={} ({})",
                interaction.user.id, sel, getattr(channel, "name", "?"),
            )
        except Exception as e:
            logger.exception(
                "Dashboard join failed user={} target={}: {}",
                interaction.user.id, sel, e,
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"Failed to join: {type(e).__name__}: {e}", ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"Failed to join: {type(e).__name__}: {e}", ephemeral=True,
                )

    @discord.ui.button(
        label="Leave", style=discord.ButtonStyle.secondary, row=2,
        custom_id="dashboard:v2:leave",
    )
    async def leave_button(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        sel = self._get_selection(interaction)
        if not sel or sel == "none":
            await interaction.response.send_message(
                "Pick a team channel from the dropdown above first.",
                ephemeral=True,
            )
            return
        try:
            channel = interaction.client.get_channel(int(sel))
            if channel is None:
                channel = await interaction.client.fetch_channel(int(sel))
            guild = channel.guild
            member = guild.get_member(interaction.user.id)
            if member is None:
                member = await guild.fetch_member(interaction.user.id)
            await channel.set_permissions(
                member, overwrite=None, reason="Organizer left via dashboard",
            )
            await interaction.response.send_message(
                f"Left <#{sel}>.", ephemeral=True,
            )
        except Exception as e:
            logger.exception(
                "Dashboard leave failed user={} target={}: {}",
                interaction.user.id, sel, e,
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"Failed to leave: {type(e).__name__}: {e}", ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"Failed to leave: {type(e).__name__}: {e}", ephemeral=True,
                )

    @discord.ui.button(
        label="Refresh", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=2,
        custom_id="dashboard:v2:refresh",
    )
    async def refresh_button(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        from nanobot.helpqueue.handler import get_store

        store = get_store()

        # Recount tickets
        open_count = sum(
            1 for t in store._tickets.values() if t.status == "open"
        )
        claimed_count = sum(
            1 for t in store._tickets.values() if t.status == "claimed"
        )
        resolved_count = sum(
            1 for t in store._tickets.values() if t.status == "resolved"
        )

        # Refetch team channels from category
        guild = interaction.guild
        team_channels: list[tuple[str, str]] = []
        if guild:
            category = guild.get_channel(int(self.teams_category_id))
            if category:
                team_channels = [
                    (str(ch.id), ch.name)
                    for ch in category.channels
                    if isinstance(ch, discord.TextChannel)
                ]

        embed = build_dashboard_embed(
            open_count, claimed_count, resolved_count, len(team_channels),
        )
        new_view = DashboardView(
            team_channels=team_channels,
            teams_category_id=self.teams_category_id,
            help_queue_channel_id=self.help_queue_channel_id,
            mentor_role_id=self.mentor_role_id,
        )
        await interaction.response.edit_message(embed=embed, view=new_view)
