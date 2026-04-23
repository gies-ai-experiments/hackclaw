"""Invite → role auto-assignment for the Discord channel.

Discord's REST API doesn't let you attach a role to an invite link
directly. To achieve "click this invite → get a specific role", we:

1. Maintain a JSON mapping ``{invite_code: role_id}`` on disk (so the
   assignment survives bot restarts) in :data:`_MAP_PATH`.
2. On ``on_ready``, cache the current ``uses`` count of every invite in
   every guild the bot is in.
3. On ``on_member_join``, refetch invites, diff against the cache to
   find the single invite whose ``uses`` incremented, look that invite
   code up in the mapping, and assign the recorded role to the member.

Caveats
-------
- The bot needs **Manage Guild** to list invites and **Manage Roles**
  plus a higher position in the role hierarchy than the role it
  assigns. Failure logs the specific reason but never raises into the
  gateway.
- If two people join via two different invites between polls, we can
  still detect both (each invite's uses went up by 1). If the same
  invite is used twice (shouldn't happen with max_uses=1), the second
  join won't get a role because the cache already shows the bumped
  count. That's the intended semantics for single-use invites.
- Vanity URLs aren't tracked (Discord doesn't expose their uses via the
  invites endpoint); they'll fall back to no role.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    import discord


_MAP_PATH = Path("/opt/hackclaw/logs/invite-role-map.json")


def _load_map() -> dict[str, str]:
    try:
        return dict(json.loads(_MAP_PATH.read_text()))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("invite-role map unreadable ({}); starting fresh", exc)
        return {}


def _save_map(mapping: dict[str, str]) -> None:
    _MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MAP_PATH.write_text(json.dumps(mapping, indent=2, sort_keys=True))


class InviteTracker:
    """In-memory cache of per-guild invite use counts + the role mapping.

    One instance per ``DiscordBotClient``. Not thread-safe but everything
    runs on discord.py's single event loop so that's fine.
    """

    def __init__(self) -> None:
        # guild_id -> {invite_code: uses}
        self._uses: dict[int, dict[str, int]] = {}
        self._mapping: dict[str, str] = _load_map()

    # --- persistence ---
    def reload_mapping(self) -> None:
        self._mapping = _load_map()

    def add_mapping(self, code: str, role_id: str) -> None:
        self._mapping[code] = str(role_id)
        _save_map(self._mapping)

    def remove_mapping(self, code: str) -> None:
        self._mapping.pop(code, None)
        _save_map(self._mapping)

    def mapping_snapshot(self) -> dict[str, str]:
        return dict(self._mapping)

    # --- cache ---
    async def refresh_guild(self, guild: "discord.Guild") -> None:
        """Snapshot current invite uses for *guild*. Swallows API failures."""
        try:
            invites = await guild.invites()
        except Exception as exc:
            logger.warning("InviteTracker: can't list invites for guild {}: {}", guild.id, exc)
            self._uses[guild.id] = {}
            return
        self._uses[guild.id] = {inv.code: (inv.uses or 0) for inv in invites}

    async def used_code_for_member(self, member: "discord.Member") -> str | None:
        """Return the invite code *member* just joined through, if identifiable."""
        guild = member.guild
        try:
            current = await guild.invites()
        except Exception as exc:
            logger.warning("InviteTracker: can't list invites after join: {}", exc)
            return None
        before = self._uses.get(guild.id, {})
        used: str | None = None
        for inv in current:
            code = inv.code
            cur = inv.uses or 0
            prev = before.get(code, 0)
            if cur > prev:
                used = code
                break
        # Update the snapshot either way so we don't double-assign
        self._uses[guild.id] = {inv.code: (inv.uses or 0) for inv in current}
        return used

    def role_for_code(self, code: str) -> str | None:
        return self._mapping.get(code)


async def on_member_join_assign_role(
    tracker: InviteTracker,
    member: "discord.Member",
) -> None:
    """Handler: identify the invite used and assign the mapped role."""
    code = await tracker.used_code_for_member(member)
    if code is None:
        logger.info(
            "Member joined but invite source couldn't be identified: {} ({})",
            member.display_name, member.id,
        )
        return
    role_id = tracker.role_for_code(code)
    if role_id is None:
        logger.info(
            "Member {} joined via invite {!r}; no role mapping — skip assign",
            member.id, code,
        )
        return
    role = member.guild.get_role(int(role_id))
    if role is None:
        logger.warning(
            "Mapped role {} for invite {!r} not found in guild {}",
            role_id, code, member.guild.id,
        )
        return
    try:
        await member.add_roles(role, reason=f"auto-assigned from invite {code}")
        logger.info(
            "Auto-role assigned: member={} ({}) role={} via invite={}",
            member.display_name, member.id, role.name, code,
        )
    except Exception as exc:
        logger.exception(
            "Failed to assign role {} to member {} via invite {}: {}",
            role_id, member.id, code, exc,
        )
