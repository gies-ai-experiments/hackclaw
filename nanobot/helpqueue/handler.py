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
_reminder_tasks: dict[str, asyncio.Task[None]] = {}  # ticket_id -> reminder task


def get_store() -> TicketStore:
    """Return the module-level singleton TicketStore, creating it on first call."""
    global _store
    if _store is None:
        _store = TicketStore()
    return _store


# ---------------------------------------------------------------------------
# /helpme (instant — slash command with parameters)
# ---------------------------------------------------------------------------


async def helpme_instant(
    interaction: discord.Interaction,
    location: str,
    problem: str,
    help_queue_channel_id: str,
    mentor_role_id: str,
) -> None:
    """Single-shot /helpme with location and problem as slash command params."""
    store = get_store()
    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message(
            "This command must be used in a text channel.", ephemeral=True
        )
        return

    channel_id = channel.id

    # Check for existing open ticket
    existing = store.get_open_by_channel(channel_id)
    if existing is not None:
        await interaction.response.send_message(
            f"This channel already has an open help request ({existing.id}). "
            "Use `/resolved` to close it before opening a new one.",
            ephemeral=True,
        )
        return

    # Auto-detect team name from channel name
    team_name = channel.name.replace("-", " ").title() if hasattr(channel, "name") else "Unknown Team"

    # Check for similar past solutions
    from pathlib import Path
    from nanobot.helpqueue.solutions import SolutionStore, generate_embedding

    solutions_path = Path(__file__).resolve().parent.parent.parent / "brain" / "solutions.json"
    solution_store = SolutionStore(solutions_path)

    if solution_store.all():
        embedding = await generate_embedding(problem.strip())
        if embedding:
            matches = solution_store.find_similar(embedding, threshold=0.75)
            if matches:
                best_match, score = matches[0]
                from nanobot.helpqueue.views import SuggestionView
                view = SuggestionView(
                    location=location.strip(),
                    problem=problem.strip(),
                    help_queue_channel_id=help_queue_channel_id,
                    mentor_role_id=mentor_role_id,
                    channel_id=channel_id,
                    team_name=team_name,
                )
                await interaction.response.send_message(
                    f"\U0001f4a1 A similar issue was resolved before:\n"
                    f"> **{best_match.ticket_id}:** \"{best_match.solution}\"\n\n"
                    f"Have you tried this?",
                    view=view,
                )
                return

    # Create ticket
    ticket = store.create(
        team_name=team_name,
        channel_id=channel_id,
        location=location.strip(),
        description=problem.strip(),
    )

    # Confirm in team channel
    await interaction.response.send_message(
        f"Help request **{ticket.id}** created! A mentor will be with you shortly.\n"
        f"**Location:** {ticket.location}\n**Problem:** {ticket.description}"
    )

    # Post embed + ClaimView to #help-queue
    try:
        client = interaction.client
        queue_channel = client.get_channel(int(help_queue_channel_id))
        if queue_channel is None:
            queue_channel = await client.fetch_channel(int(help_queue_channel_id))

        embed = build_ticket_embed(ticket)
        view = ClaimView(ticket.id)
        ping = f"<@&{mentor_role_id}> New help request!" if mentor_role_id else "New help request!"
        queue_msg = await queue_channel.send(content=ping, embed=embed, view=view)
        ticket.queue_message_id = queue_msg.id
    except Exception as e:
        logger.warning("Failed to post help ticket to queue channel: {}", e)

    # Schedule reminder
    task = asyncio.create_task(
        _reminder_loop(ticket, help_queue_channel_id, mentor_role_id, interaction.client)
    )
    _reminder_tasks[ticket.id] = task


# ---------------------------------------------------------------------------
# Channel join / leave helpers
# ---------------------------------------------------------------------------


async def _add_mentor_to_channel(client: discord.Client, channel_id: int, user: discord.User | discord.Member) -> None:
    """Grant a mentor access to a team channel via permission overwrite."""
    try:
        channel = client.get_channel(channel_id)
        if channel is None:
            channel = await client.fetch_channel(channel_id)
        await channel.set_permissions(
            user,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            reason="Mentor claimed help ticket",
        )
    except Exception as e:
        logger.warning("Failed to add mentor to channel {}: {}", channel_id, e)


async def _remove_mentor_from_channel(client: discord.Client, channel_id: int, user_id: str) -> None:
    """Remove a mentor's permission overwrite from a team channel."""
    try:
        channel = client.get_channel(channel_id)
        if channel is None:
            channel = await client.fetch_channel(channel_id)
        guild = channel.guild
        member = guild.get_member(int(user_id))
        if member is None:
            member = await guild.fetch_member(int(user_id))
        await channel.set_permissions(member, overwrite=None, reason="Help ticket resolved/unclaimed")
    except Exception as e:
        logger.warning("Failed to remove mentor from channel {}: {}", channel_id, e)


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

    # Update queue embed: disable Claim, enable Unclaim + Resolve
    embed = build_ticket_embed(ticket)
    view = ClaimView(ticket.id)
    view.claim_button.disabled = True
    view.unclaim_button.disabled = False
    view.resolve_button.disabled = False
    await interaction.response.edit_message(embed=embed, view=view)

    # Add mentor to team channel
    await _add_mentor_to_channel(interaction.client, ticket.channel_id, interaction.user)

    # Notify the team channel
    try:
        team_channel = interaction.client.get_channel(ticket.channel_id)
        if team_channel is None:
            team_channel = await interaction.client.fetch_channel(ticket.channel_id)
        await team_channel.send(
            f"Mentor **{mentor_name}** has joined the channel to help with {ticket.id}!"
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

    # Update queue embed: enable Claim, disable Unclaim + Resolve
    embed = build_ticket_embed(ticket)
    view = ClaimView(ticket.id)
    view.claim_button.disabled = False
    view.unclaim_button.disabled = True
    view.resolve_button.disabled = True
    await interaction.response.edit_message(embed=embed, view=view)

    # Remove mentor from team channel
    await _remove_mentor_from_channel(interaction.client, ticket.channel_id, mentor_id)

    # Notify the team channel
    try:
        team_channel = interaction.client.get_channel(ticket.channel_id)
        if team_channel is None:
            team_channel = await interaction.client.fetch_channel(ticket.channel_id)
        await team_channel.send(
            f"Mentor left the channel. {ticket.id} is back in the queue — waiting for another mentor."
        )
    except Exception as e:
        logger.warning("Failed to notify team channel for ticket {}: {}", ticket_id, e)


async def handle_resolve_button(
    interaction: discord.Interaction,
    ticket_id: str,
    help_queue_channel_id: str,
) -> None:
    """Show resolve modal when mentor clicks Resolve button."""
    store = get_store()
    ticket = store.get(ticket_id)
    if ticket is None:
        await interaction.response.send_message("Ticket not found.", ephemeral=True)
        return

    user_id = str(interaction.user.id)
    if ticket.claimed_by != user_id:
        await interaction.response.send_message(
            f"Only the claiming mentor (**{ticket.claimed_by_name}**) can resolve this ticket.",
            ephemeral=True,
        )
        return

    from nanobot.helpqueue.views import ResolveModal
    modal = ResolveModal(ticket_id, help_queue_channel_id)
    await interaction.response.send_modal(modal)


async def handle_resolve_with_solution(
    interaction: discord.Interaction,
    ticket_id: str,
    solution_text: str,
    help_queue_channel_id: str,
) -> None:
    """Resolve a ticket and save the mentor's solution."""
    store = get_store()
    ticket = store.get(ticket_id)
    if ticket is None:
        await interaction.response.send_message("Ticket not found.", ephemeral=True)
        return

    mentor_id = ticket.claimed_by

    success = store.resolve(ticket_id)
    if not success:
        await interaction.response.send_message(
            "Could not resolve — ticket may already be resolved.", ephemeral=True
        )
        return

    # Save solution text on ticket
    resolved_ticket = store.get(ticket_id) or ticket
    resolved_ticket.solution = solution_text

    # Cancel pending reminder
    reminder_task = _reminder_tasks.pop(ticket_id, None)
    if reminder_task is not None and not reminder_task.done():
        reminder_task.cancel()

    # Update queue embed: show resolved, remove buttons
    embed = build_ticket_embed(resolved_ticket)
    await interaction.response.edit_message(embed=embed, view=None)

    # Remove mentor from team channel
    if mentor_id:
        await _remove_mentor_from_channel(interaction.client, ticket.channel_id, mentor_id)

    # Notify team channel
    time_msg = ""
    if resolved_ticket.created_at and resolved_ticket.resolved_at:
        minutes = int((resolved_ticket.resolved_at - resolved_ticket.created_at).total_seconds() / 60)
        time_msg = f" (resolved in {minutes} min)"

    try:
        team_channel = interaction.client.get_channel(ticket.channel_id)
        if team_channel is None:
            team_channel = await interaction.client.fetch_channel(ticket.channel_id)
        await team_channel.send(
            f"Help request **{ticket.id}** has been resolved!{time_msg} Mentor has left the channel."
        )
    except Exception as e:
        logger.warning("Failed to notify team channel for ticket {}: {}", ticket_id, e)

    # Save solution with embedding (async, non-blocking)
    asyncio.create_task(_save_solution(resolved_ticket, solution_text))


# ---------------------------------------------------------------------------
# Solution persistence
# ---------------------------------------------------------------------------


async def _save_solution(ticket: HelpTicket, solution_text: str) -> None:
    """Generate embedding and persist solution to the knowledge base."""
    from pathlib import Path
    from nanobot.helpqueue.solutions import SolutionStore, generate_embedding

    solutions_path = Path(__file__).resolve().parent.parent.parent / "brain" / "solutions.json"
    solution_store = SolutionStore(solutions_path)

    embedding = await generate_embedding(f"{ticket.description} | {solution_text}")
    solution_store.add(
        ticket_id=ticket.id,
        problem=ticket.description,
        solution=solution_text,
        embedding=embedding,
    )
    logger.info("Saved solution for ticket {} with embedding ({} dims)", ticket.id, len(embedding))


# ---------------------------------------------------------------------------
# Create ticket from suggestion flow
# ---------------------------------------------------------------------------


async def create_ticket_from_suggestion(
    interaction: discord.Interaction,
    location: str,
    problem: str,
    help_queue_channel_id: str,
    mentor_role_id: str,
    channel_id: int,
    team_name: str,
) -> None:
    """Create a ticket after the user confirms the suggestion didn't help."""
    store = get_store()

    ticket = store.create(
        team_name=team_name,
        channel_id=channel_id,
        location=location,
        description=problem,
    )

    # Confirm in team channel
    channel = interaction.client.get_channel(channel_id)
    if channel is None:
        channel = await interaction.client.fetch_channel(channel_id)
    await channel.send(
        f"Help request **{ticket.id}** created! A mentor will be with you shortly.\n"
        f"**Location:** {ticket.location}\n**Problem:** {ticket.description}"
    )

    # Post embed to #help-queue
    try:
        queue_channel = interaction.client.get_channel(int(help_queue_channel_id))
        if queue_channel is None:
            queue_channel = await interaction.client.fetch_channel(int(help_queue_channel_id))

        embed = build_ticket_embed(ticket)
        view = ClaimView(ticket.id)
        ping = f"<@&{mentor_role_id}> New help request!" if mentor_role_id else "New help request!"
        queue_msg = await queue_channel.send(content=ping, embed=embed, view=view)
        ticket.queue_message_id = queue_msg.id
    except Exception as e:
        logger.warning("Failed to post help ticket to queue channel: {}", e)

    # Schedule reminder
    task = asyncio.create_task(
        _reminder_loop(ticket, help_queue_channel_id, mentor_role_id, interaction.client)
    )
    _reminder_tasks[ticket.id] = task


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

    # Only the mentor who claimed the ticket can resolve it
    user_id = str(interaction.user.id)
    if ticket.status == "claimed" and ticket.claimed_by != user_id:
        await interaction.response.send_message(
            f"Only the claiming mentor (**{ticket.claimed_by_name}**) can resolve this ticket.",
            ephemeral=True,
        )
        return

    if ticket.status == "open":
        await interaction.response.send_message(
            "This ticket hasn't been claimed by a mentor yet. A mentor must claim it first.",
            ephemeral=True,
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
