"""Discord UI components for the help ticket queue."""

from __future__ import annotations

import discord

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
    """Build the organizer dashboard embed."""
    embed = discord.Embed(
        title="Hackclaw Organizer Dashboard",
        colour=discord.Colour(0x9B59B6),
    )
    embed.add_field(
        name="Help Queue",
        value=(
            f"\U0001f7e0 Open: **{open_count}**\n"
            f"\U0001f535 Claimed: **{claimed_count}**\n"
            f"\U0001f7e2 Resolved: **{resolved_count}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Team Channels",
        value=f"**{team_count}** teams found",
        inline=True,
    )
    return embed


class DashboardView(discord.ui.View):
    """Organizer dashboard with team channel join/leave and ticket stats."""

    def __init__(
        self,
        team_channels: list[tuple[str, str]],
        teams_category_id: str,
        help_queue_channel_id: str,
        mentor_role_id: str,
    ) -> None:
        super().__init__(timeout=300)
        self.teams_category_id = teams_category_id
        self.help_queue_channel_id = help_queue_channel_id
        self.mentor_role_id = mentor_role_id
        self.selected_channel_id: str | None = None

        # Build select menu from team channels
        if team_channels:
            options = [
                discord.SelectOption(label=name, value=cid)
                for cid, name in team_channels[:25]  # Discord max 25 options
            ]
        else:
            options = [discord.SelectOption(label="No teams found", value="none")]

        self.channel_select.options = options

    @discord.ui.select(placeholder="Select a team channel...")
    async def channel_select(
        self, interaction: discord.Interaction, select: discord.ui.Select,
    ) -> None:
        self.selected_channel_id = select.values[0] if select.values else None
        await interaction.response.defer()

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success, row=2)
    async def join_button(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        if not self.selected_channel_id or self.selected_channel_id == "none":
            await interaction.response.send_message(
                "Select a team channel first.", ephemeral=True,
            )
            return
        try:
            channel = interaction.client.get_channel(int(self.selected_channel_id))
            if channel is None:
                channel = await interaction.client.fetch_channel(
                    int(self.selected_channel_id),
                )
            await channel.set_permissions(
                interaction.user,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                reason="Organizer joined via dashboard",
            )
            await interaction.response.send_message(
                f"Joined <#{self.selected_channel_id}>!", ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to join: {e}", ephemeral=True,
            )

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary, row=2)
    async def leave_button(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        if not self.selected_channel_id or self.selected_channel_id == "none":
            await interaction.response.send_message(
                "Select a team channel first.", ephemeral=True,
            )
            return
        try:
            channel = interaction.client.get_channel(int(self.selected_channel_id))
            if channel is None:
                channel = await interaction.client.fetch_channel(
                    int(self.selected_channel_id),
                )
            guild = channel.guild
            member = guild.get_member(interaction.user.id)
            if member is None:
                member = await guild.fetch_member(interaction.user.id)
            await channel.set_permissions(
                member, overwrite=None, reason="Organizer left via dashboard",
            )
            await interaction.response.send_message(
                f"Left <#{self.selected_channel_id}>.", ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to leave: {e}", ephemeral=True,
            )

    @discord.ui.button(
        label="Refresh", style=discord.ButtonStyle.primary, emoji="\U0001f504", row=2,
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
