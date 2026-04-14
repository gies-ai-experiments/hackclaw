"""Handler functions for help-queue slash commands and button interactions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import discord
from loguru import logger

from nanobot.helpqueue.ticket import HelpTicket, TicketStore
from nanobot.helpqueue.views import ClaimView, build_ticket_embed

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Module-level singleton store
# ---------------------------------------------------------------------------

_store: TicketStore | None = None
_active_flows: dict[int, bool] = {}  # channel_id -> True while flow is running
_reminder_tasks: dict[str, asyncio.Task[None]] = {}  # ticket_id -> reminder task


def get_store() -> TicketStore:
    """Return the module-level singleton TicketStore, creating it on first call."""
    global _store
    if _store is None:
        _store = TicketStore()
    return _store


# ---------------------------------------------------------------------------
# /helpme flow
# ---------------------------------------------------------------------------


async def helpme_flow(
    interaction: discord.Interaction,
    help_queue_channel_id: str,
    mentor_role_id: str,
) -> None:
    """Multi-step flow that collects location + description, creates a ticket,
    posts an embed to #help-queue, and schedules a reminder ping."""
    store = get_store()
    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message(
            "This command must be used in a text channel.", ephemeral=True
        )
        return

    channel_id = channel.id

    # 1. Check for existing open ticket
    existing = store.get_open_by_channel(channel_id)
    if existing is not None:
        await interaction.response.send_message(
            f"This channel already has an open help request ({existing.id}). "
            "Use `/resolved` to close it before opening a new one.",
            ephemeral=True,
        )
        return

    # 2. Prevent concurrent flows in the same channel
    if _active_flows.get(channel_id):
        await interaction.response.send_message(
            "A help request is already being created in this channel. Please wait.",
            ephemeral=True,
        )
        return

    _active_flows[channel_id] = True
    try:
        await _run_helpme_flow(interaction, channel, channel_id, help_queue_channel_id, mentor_role_id, store)
    finally:
        _active_flows.pop(channel_id, None)


async def _run_helpme_flow(
    interaction: discord.Interaction,
    channel: Any,
    channel_id: int,
    help_queue_channel_id: str,
    mentor_role_id: str,
    store: TicketStore,
) -> None:
    """Inner flow logic, separated so the active-flow lock is always released."""
    user = interaction.user
    client = interaction.client
    timeout = 300.0  # 5 minutes

    # 3. Ask for location
    await interaction.response.send_message(
        "Where are you on campus? (e.g., BIF 2007, Wohlers 215)"
    )

    def check_author(m: discord.Message) -> bool:
        return m.author.id == user.id and m.channel.id == channel_id

    try:
        location_msg = await client.wait_for("message", check=check_author, timeout=timeout)
    except asyncio.TimeoutError:
        await channel.send("Help request timed out. Run `/helpme` again when ready.")
        return
    location = location_msg.content.strip()

    # 5. Ask for description
    await channel.send("What do you need help with?")

    try:
        description_msg = await client.wait_for("message", check=check_author, timeout=timeout)
    except asyncio.TimeoutError:
        await channel.send("Help request timed out. Run `/helpme` again when ready.")
        return
    description = description_msg.content.strip()

    # 7. Auto-detect team name from channel name
    team_name = channel.name.replace("-", " ").title() if hasattr(channel, "name") else "Unknown Team"

    # 8. Create ticket
    ticket = store.create(
        team_name=team_name,
        channel_id=channel_id,
        location=location,
        description=description,
    )

    # 9. Confirm in team channel
    await channel.send(
        f"Help request **{ticket.id}** created! A mentor will be with you shortly."
    )

    # 10. Post embed + ClaimView to #help-queue channel, ping @TechMentor
    try:
        queue_channel = client.get_channel(int(help_queue_channel_id))
        if queue_channel is None:
            queue_channel = await client.fetch_channel(int(help_queue_channel_id))

        embed = build_ticket_embed(ticket)
        view = ClaimView(ticket.id)
        queue_msg = await queue_channel.send(
            content=f"<@&{mentor_role_id}> New help request!",
            embed=embed,
            view=view,
        )
        ticket.queue_message_id = queue_msg.id
    except Exception as e:
        logger.warning("Failed to post help ticket to queue channel: {}", e)

    # 11. Schedule reminder task
    task = asyncio.create_task(
        _reminder_loop(ticket, help_queue_channel_id, mentor_role_id, client)
    )
    _reminder_tasks[ticket.id] = task


# ---------------------------------------------------------------------------
# Claim / Unclaim handlers
# ---------------------------------------------------------------------------


async def handle_claim(interaction: discord.Interaction, ticket_id: str) -> None:
    """Claim a ticket on behalf of the interacting mentor."""
    store = get_store()
    mentor_id = str(interaction.user.id)
    mentor_name = interaction.user.display_name

    success = store.claim(ticket_id, mentor_id=mentor_id, mentor_name=mentor_name)
    if not success:
        await interaction.response.send_message(
            "Could not claim this ticket. It may already be claimed or resolved.",
            ephemeral=True,
        )
        return

    ticket = store.get(ticket_id)
    if ticket is None:
        await interaction.response.send_message("Ticket not found.", ephemeral=True)
        return

    # Update queue embed: disable Claim, enable Unclaim
    embed = build_ticket_embed(ticket)
    view = ClaimView(ticket.id)
    view.claim_button.disabled = True
    view.unclaim_button.disabled = False
    await interaction.response.edit_message(embed=embed, view=view)

    # Notify the team channel
    try:
        team_channel = interaction.client.get_channel(ticket.channel_id)
        if team_channel is None:
            team_channel = await interaction.client.fetch_channel(ticket.channel_id)
        await team_channel.send(
            f"Mentor **{mentor_name}** is on the way to help with {ticket.id}!"
        )
    except Exception as e:
        logger.warning("Failed to notify team channel for ticket {}: {}", ticket_id, e)


async def handle_unclaim(interaction: discord.Interaction, ticket_id: str) -> None:
    """Release a claimed ticket back to the open queue."""
    store = get_store()
    mentor_id = str(interaction.user.id)

    success = store.unclaim(ticket_id, mentor_id=mentor_id)
    if not success:
        await interaction.response.send_message(
            "Could not unclaim this ticket. You may not be the current claimant.",
            ephemeral=True,
        )
        return

    ticket = store.get(ticket_id)
    if ticket is None:
        await interaction.response.send_message("Ticket not found.", ephemeral=True)
        return

    # Update queue embed: enable Claim, disable Unclaim
    embed = build_ticket_embed(ticket)
    view = ClaimView(ticket.id)
    view.claim_button.disabled = False
    view.unclaim_button.disabled = True
    await interaction.response.edit_message(embed=embed, view=view)

    # Notify the team channel
    try:
        team_channel = interaction.client.get_channel(ticket.channel_id)
        if team_channel is None:
            team_channel = await interaction.client.fetch_channel(ticket.channel_id)
        await team_channel.send(
            f"Mentor released {ticket.id} — waiting for another mentor to claim."
        )
    except Exception as e:
        logger.warning("Failed to notify team channel for ticket {}: {}", ticket_id, e)


# ---------------------------------------------------------------------------
# /resolved command
# ---------------------------------------------------------------------------


async def handle_resolve_command(
    interaction: discord.Interaction,
    help_queue_channel_id: str,
) -> None:
    """Mark the open ticket for this channel as resolved."""
    store = get_store()
    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message(
            "This command must be used in a text channel.", ephemeral=True
        )
        return

    ticket = store.get_open_by_channel(channel.id)
    if ticket is None:
        await interaction.response.send_message(
            "No open help request found for this channel.", ephemeral=True
        )
        return

    success = store.resolve(ticket.id)
    if not success:
        await interaction.response.send_message(
            "Could not resolve the ticket — it may already be resolved.",
            ephemeral=True,
        )
        return

    # Cancel pending reminder
    reminder_task = _reminder_tasks.pop(ticket.id, None)
    if reminder_task is not None and not reminder_task.done():
        reminder_task.cancel()

    # Show resolution time in team channel
    resolved_ticket = store.get(ticket.id)
    if resolved_ticket and resolved_ticket.created_at and resolved_ticket.resolved_at:
        delta = resolved_ticket.resolved_at - resolved_ticket.created_at
        minutes = int(delta.total_seconds() / 60)
        time_msg = f" (resolved in {minutes} min)"
    else:
        time_msg = ""

    await interaction.response.send_message(
        f"Help request **{ticket.id}** has been resolved!{time_msg}"
    )

    # Update the queue embed: remove buttons, show resolved state
    try:
        queue_channel = interaction.client.get_channel(int(help_queue_channel_id))
        if queue_channel is None:
            queue_channel = await interaction.client.fetch_channel(int(help_queue_channel_id))

        if ticket.queue_message_id:
            queue_msg = await queue_channel.fetch_message(ticket.queue_message_id)
            embed = build_ticket_embed(store.get(ticket.id) or ticket)
            await queue_msg.edit(embed=embed, view=None)
    except Exception as e:
        logger.warning("Failed to update queue embed for ticket {}: {}", ticket.id, e)


# ---------------------------------------------------------------------------
# Reminder loop
# ---------------------------------------------------------------------------


async def _reminder_loop(
    ticket: HelpTicket,
    help_queue_channel_id: str,
    mentor_role_id: str,
    client: Any,
    reminder_minutes: int = 10,
) -> None:
    """Async task that re-pings if a ticket is still open after *reminder_minutes*."""
    try:
        await asyncio.sleep(reminder_minutes * 60)
    except asyncio.CancelledError:
        return

    store = get_store()
    current = store.get(ticket.id)
    if current is None or current.status != "open":
        return

    try:
        queue_channel = client.get_channel(int(help_queue_channel_id))
        if queue_channel is None:
            queue_channel = await client.fetch_channel(int(help_queue_channel_id))
        await queue_channel.send(
            f"<@&{mentor_role_id}> Reminder: **{ticket.id}** ({ticket.team_name}) "
            f"has been waiting for {reminder_minutes} minutes!"
        )
    except Exception as e:
        logger.warning("Failed to send reminder for ticket {}: {}", ticket.id, e)
    finally:
        _reminder_tasks.pop(ticket.id, None)
