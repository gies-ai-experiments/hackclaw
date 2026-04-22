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
    *,
    location: str,
    problem: str,
    mode: str,
    help_queue_channel_id: str,
    mentor_role_id: str,
    office_hours_voice_ids: list[str] | None = None,
) -> None:
    """Single-shot /helpme.

    *mode* is ``"in_person"`` or ``"online"``. For online tickets,
    ``office_hours_voice_ids`` seeds the room pool used when a mentor
    claims — without it, claim will fail with "no rooms configured".
    """
    store = get_store()
    if mode == "online" and office_hours_voice_ids:
        store.configure_rooms(list(office_hours_voice_ids))

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message(
            "This command must be used in a text channel.", ephemeral=True
        )
        return

    channel_id = channel.id

    # For in-person, only one open ticket per team channel. Online does not
    # use the team channel for delivery, so the per-channel lock doesn't apply.
    if mode == "in_person":
        existing = store.get_open_by_channel(channel_id)
        if existing is not None:
            await interaction.response.send_message(
                f"This channel already has an open help request ({existing.id}). "
                "Use `/resolved` to close it before opening a new one.",
                ephemeral=True,
            )
            return

    if mode == "in_person" and not location.strip():
        await interaction.response.send_message(
            "Please include a location for in-person help (e.g. 'BIF 2007').",
            ephemeral=True,
        )
        return

    # Online must be fired from a team channel — the bot uses that channel's
    # member list to figure out who on the team should get voice access.
    if mode == "online":
        parent_id = getattr(channel, "category_id", None)
        if parent_id is None or str(parent_id) != TEAMS_CATEGORY_ID:
            await interaction.response.send_message(
                "Run `/helpme mode:online` from your **team's text channel** "
                "(under the Teams category). That tells me who's on your team "
                "so I can give everyone voice access — running it from a "
                "different channel would either expose the voice to the "
                "whole guild or leave your teammates locked out.",
                ephemeral=True,
            )
            return

    # Auto-detect team name from channel name
    team_name = channel.name.replace("-", " ").title() if hasattr(channel, "name") else "Unknown Team"

    # Check for similar past solutions (shared across both modes)
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

    participant_id = str(interaction.user.id)

    ticket = store.create(
        team_name=team_name,
        channel_id=channel_id,
        location=location.strip(),
        description=problem.strip(),
        mode="online" if mode == "online" else "in_person",
        participant_id=participant_id,
    )
    queue_position = -1
    if ticket.mode == "online":
        queue_position = store.enqueue_online(ticket.id)

    # Confirm to the participant (ephemeral for online — no need to clutter team channel)
    if ticket.mode == "online":
        configured = store.configured_rooms()
        rooms_info = (
            f"When a mentor claims your ticket, one of the office-hours voice "
            f"channels ({', '.join(f'<#{r}>' for r in configured)}) will become "
            f"visible in your sidebar — join it then."
            if configured
            else "Office-hours rooms aren't configured yet — ask an organizer."
        )
        await interaction.response.send_message(
            f"Online help request **{ticket.id}** — you're **position #{queue_position}** in the queue.\n"
            f"**Problem:** {ticket.description}\n\n{rooms_info}",
            ephemeral=True,
        )
    else:
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

        embed = build_ticket_embed(
            ticket,
            queue_position=queue_position if ticket.mode == "online" else None,
        )
        view = ClaimView(ticket.id)
        if mentor_role_id:
            mode_label = "online" if ticket.mode == "online" else "in-person"
            ping = f"<@&{mentor_role_id}> new {mode_label} help request!"
        else:
            ping = "New help request!"
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
# Online office-hours helpers (voice channel permission grants)
# ---------------------------------------------------------------------------

# Team channels live under this category in the Gies guild. /helpme online
# must be fired from a channel in this category so the bot can use its
# member list as the team roster.
TEAMS_CATEGORY_ID = "1493806352139817172"


async def _team_member_ids(client: discord.Client, text_channel_id: int) -> list[str]:
    """Return Discord user ids of everyone who can see *text_channel_id*.

    Mirrors the team roster: anyone who has view_channel on the team's text
    channel is treated as a team member and gets voice access on claim.
    Bots are filtered out so the bot doesn't try to grant itself access.
    """
    try:
        channel = client.get_channel(text_channel_id)
        if channel is None:
            channel = await client.fetch_channel(text_channel_id)
        members = getattr(channel, "members", None) or []
        return [str(m.id) for m in members if not m.bot]
    except Exception as e:
        logger.warning("Failed to enumerate members for channel {}: {}", text_channel_id, e)
        return []


async def _grant_voice_access(client: discord.Client, voice_channel_id: str, user_id: str) -> None:
    """Add a ``view_channel + connect`` override for *user_id* on *voice_channel_id*.

    This makes a previously hidden voice channel appear in the user's
    Discord sidebar within 1–2s of the call. Failures are logged but
    non-fatal — the user just won't see the channel.
    """
    try:
        channel = client.get_channel(int(voice_channel_id))
        if channel is None:
            channel = await client.fetch_channel(int(voice_channel_id))
        guild = channel.guild
        member = guild.get_member(int(user_id))
        if member is None:
            member = await guild.fetch_member(int(user_id))
        await channel.set_permissions(
            member,
            view_channel=True,
            connect=True,
            speak=True,
            reason="Online help ticket claimed — granting participant voice access",
        )
    except Exception as e:
        logger.warning("Failed to grant voice access to user {} on {}: {}", user_id, voice_channel_id, e)


async def _revoke_voice_access(client: discord.Client, voice_channel_id: str, user_id: str) -> None:
    """Remove the participant's override on the office-hours voice channel.

    Discord re-evaluates effective permissions within ~1s; if the user
    is still connected they'll be auto-disconnected from the voice.
    """
    try:
        channel = client.get_channel(int(voice_channel_id))
        if channel is None:
            channel = await client.fetch_channel(int(voice_channel_id))
        guild = channel.guild
        member = guild.get_member(int(user_id))
        if member is None:
            member = await guild.fetch_member(int(user_id))
        await channel.set_permissions(
            member, overwrite=None,
            reason="Online help ticket resolved/unclaimed — revoking participant voice access",
        )
    except Exception as e:
        logger.warning("Failed to revoke voice access from user {} on {}: {}", user_id, voice_channel_id, e)


async def _dm_position_updates(client: discord.Client, store: TicketStore) -> None:
    """DM every remaining online-queue participant their new 1-based position."""
    for idx, tid in enumerate(store.online_queue_snapshot(), start=1):
        t = store.get(tid)
        if t is None or not t.participant_id:
            continue
        try:
            user = client.get_user(int(t.participant_id))
            if user is None:
                user = await client.fetch_user(int(t.participant_id))
            await user.send(
                f"Queue update: your online help ticket **{t.id}** is now at **position #{idx}**."
            )
        except Exception as e:
            logger.debug("Couldn't DM {} about new queue position: {}", t.participant_id, e)


# ---------------------------------------------------------------------------
# Claim / Unclaim handlers
# ---------------------------------------------------------------------------


async def handle_claim(interaction: discord.Interaction, ticket_id: str) -> None:
    """Claim a ticket on behalf of the interacting mentor.

    For online tickets this also reserves one of the configured office-hours
    voice channels and grants the participant a ``view_channel + connect``
    override on it. If all rooms are busy, the claim is rejected with a
    hint to wait for another session to resolve.
    """
    store = get_store()
    mentor_id = str(interaction.user.id)
    mentor_name = interaction.user.display_name

    ticket = store.get(ticket_id)
    if ticket is None:
        await interaction.response.send_message("Ticket not found.", ephemeral=True)
        return

    # --- Online-specific preflight: reserve a room BEFORE flipping state ---
    reserved_room: str | None = None
    if ticket.mode == "online":
        if not store.configured_rooms():
            await interaction.response.send_message(
                "Online office-hours rooms aren't configured. Ask an organizer to "
                "set `helpQueue.officeHoursVoiceIds` on the bot config.",
                ephemeral=True,
            )
            return
        if not store.any_room_free():
            await interaction.response.send_message(
                "Both office-hours rooms are in use right now. Wait until one "
                "of the current sessions resolves, then claim again.",
                ephemeral=True,
            )
            return
        # Prefer the mentor's current voice channel if it's in the pool — lets them
        # stay put between sessions.
        prefer = None
        if (
            isinstance(interaction.user, discord.Member)
            and interaction.user.voice
            and interaction.user.voice.channel
        ):
            prefer = str(interaction.user.voice.channel.id)
        reserved_room = store.reserve_room(ticket_id, prefer_room=prefer)

    success = store.claim(ticket_id, mentor_id=mentor_id, mentor_name=mentor_name)
    if not success:
        if reserved_room is not None:
            store.release_room(ticket_id)
        await interaction.response.send_message(
            "Could not claim this ticket. It may already be claimed or resolved.",
            ephemeral=True,
        )
        return

    # Update queue embed: disable Claim, enable Unclaim + Resolve
    embed = build_ticket_embed(ticket)
    view = ClaimView(ticket.id)
    view.claim_button.disabled = True
    view.unclaim_button.disabled = False
    view.resolve_button.disabled = False
    await interaction.response.edit_message(embed=embed, view=view)

    if ticket.mode == "in_person":
        # Existing in-person flow: mentor joins the team channel
        await _add_mentor_to_channel(interaction.client, ticket.channel_id, interaction.user)
        try:
            team_channel = interaction.client.get_channel(ticket.channel_id)
            if team_channel is None:
                team_channel = await interaction.client.fetch_channel(ticket.channel_id)
            await team_channel.send(
                f"Mentor **{mentor_name}** has joined the channel to help with {ticket.id}!"
            )
        except Exception as e:
            logger.warning("Failed to notify team channel for ticket {}: {}", ticket_id, e)
        return

    # --- Online-specific post-claim: grant team members access + DM ---
    store.pop_online(ticket_id)
    if reserved_room:
        team_ids = await _team_member_ids(interaction.client, ticket.channel_id)
        # Always include the requester even if we somehow failed to enumerate
        if ticket.participant_id and ticket.participant_id not in team_ids:
            team_ids.insert(0, ticket.participant_id)
        ticket.granted_user_ids = list(team_ids)
        for uid in team_ids:
            await _grant_voice_access(interaction.client, reserved_room, uid)
        if ticket.participant_id:
            try:
                user = interaction.client.get_user(int(ticket.participant_id))
                if user is None:
                    user = await interaction.client.fetch_user(int(ticket.participant_id))
                teammate_count = max(0, len(team_ids) - 1)
                teammate_note = (
                    f" Your {teammate_count} teammate{'s' if teammate_count != 1 else ''} on "
                    f"<#{ticket.channel_id}> can join too — same channel shows up for them."
                    if teammate_count > 0
                    else ""
                )
                await user.send(
                    f"You're up. Mentor **{mentor_name}** claimed ticket **{ticket.id}**.\n"
                    f"Voice channel <#{reserved_room}> is now visible in your Discord sidebar — "
                    f"join it and the mentor will be there shortly.{teammate_note}"
                )
            except Exception as e:
                logger.debug("Couldn't DM participant {} about claim: {}", ticket.participant_id, e)

    # Tell everyone else in the queue their new position
    await _dm_position_updates(interaction.client, store)


async def handle_unclaim(interaction: discord.Interaction, ticket_id: str) -> None:
    """Release a claimed ticket back to the open queue."""
    store = get_store()
    mentor_id = str(interaction.user.id)

    ticket_before = store.get(ticket_id)
    prior_room = ticket_before.online_room_id if ticket_before else None
    prior_participant = ticket_before.participant_id if ticket_before else None
    prior_mode = ticket_before.mode if ticket_before else None

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
    # Re-enqueue online tickets so position is recomputed for the embed.
    queue_position: int | None = None
    if ticket.mode == "online":
        queue_position = store.enqueue_online(ticket.id)
    embed = build_ticket_embed(ticket, queue_position=queue_position)
    view = ClaimView(ticket.id)
    view.claim_button.disabled = False
    view.unclaim_button.disabled = True
    view.resolve_button.disabled = True
    await interaction.response.edit_message(embed=embed, view=view)

    if prior_mode == "in_person":
        await _remove_mentor_from_channel(interaction.client, ticket.channel_id, mentor_id)
        try:
            team_channel = interaction.client.get_channel(ticket.channel_id)
            if team_channel is None:
                team_channel = await interaction.client.fetch_channel(ticket.channel_id)
            await team_channel.send(
                f"Mentor left the channel. {ticket.id} is back in the queue — waiting for another mentor."
            )
        except Exception as e:
            logger.warning("Failed to notify team channel for ticket {}: {}", ticket_id, e)
        return

    # Online: release the voice room + revoke everyone the team had access for
    store.release_room(ticket.id)
    if prior_room:
        granted = list(ticket_before.granted_user_ids) if ticket_before else []
        ticket.granted_user_ids = []
        if not granted and prior_participant:
            granted = [prior_participant]
        for uid in granted:
            await _revoke_voice_access(interaction.client, prior_room, uid)
    if prior_participant:
        try:
            user = interaction.client.get_user(int(prior_participant))
            if user is None:
                user = await interaction.client.fetch_user(int(prior_participant))
            await user.send(
                f"The mentor unclaimed ticket **{ticket.id}**. You're back in the online queue — "
                "hold tight and another mentor should pick you up shortly."
            )
        except Exception as e:
            logger.debug("Couldn't DM participant {} about unclaim: {}", prior_participant, e)

    await _dm_position_updates(interaction.client, store)


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
    prior_room = ticket.online_room_id
    prior_participant = ticket.participant_id
    prior_mode = ticket.mode

    # Cancel pending reminder
    reminder_task = _reminder_tasks.pop(ticket_id, None)
    if reminder_task is not None and not reminder_task.done():
        reminder_task.cancel()

    # Update queue embed: show resolved, remove buttons
    embed = build_ticket_embed(resolved_ticket)
    await interaction.response.edit_message(embed=embed, view=None)

    if prior_mode == "in_person":
        if mentor_id:
            await _remove_mentor_from_channel(interaction.client, ticket.channel_id, mentor_id)
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
    else:
        # Online: release the voice room + revoke every team-member override
        store.release_room(ticket.id)
        if prior_room:
            granted = list(resolved_ticket.granted_user_ids)
            resolved_ticket.granted_user_ids = []
            if not granted and prior_participant:
                granted = [prior_participant]
            for uid in granted:
                await _revoke_voice_access(interaction.client, prior_room, uid)
        if prior_participant:
            try:
                user = interaction.client.get_user(int(prior_participant))
                if user is None:
                    user = await interaction.client.fetch_user(int(prior_participant))
                await user.send(
                    f"Ticket **{ticket.id}** resolved. Hope that helped — you and your team "
                    "are free to leave the voice channel; the office-hours room has been "
                    "removed from everyone's sidebar."
                )
            except Exception as e:
                logger.debug("Couldn't DM participant {} about resolve: {}", prior_participant, e)
        # Anyone still in the online queue just moved up (or stayed put) — refresh their positions
        await _dm_position_updates(interaction.client, store)

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
