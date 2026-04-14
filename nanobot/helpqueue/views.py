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
