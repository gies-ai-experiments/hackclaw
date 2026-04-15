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


def build_ticket_embed(ticket: HelpTicket) -> discord.Embed:
    """Build a Discord embed for a help ticket."""
    embed = discord.Embed(
        title=f"{ticket.id} — {ticket.team_name}",
        colour=STATUS_COLOURS.get(ticket.status, discord.Colour.greyple()),
    )
    embed.add_field(name="Location", value=ticket.location, inline=True)
    embed.add_field(name="Status", value=STATUS_LABELS.get(ticket.status, ticket.status), inline=True)
    embed.add_field(name="Description", value=ticket.description, inline=False)

    if ticket.claimed_by_name:
        embed.add_field(name="Claimed by", value=ticket.claimed_by_name, inline=True)

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
