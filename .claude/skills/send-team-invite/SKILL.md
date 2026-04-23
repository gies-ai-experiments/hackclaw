---
name: send-team-invite
description: Create a Discord team role + private team channel + single-use invite, map the invite to auto-assign roles on join, and email the participant with the link. Use for every participant/team getting onboarded into the Gies AI for Impact Challenge Discord.
---

# send-team-invite

## When to use this skill

Any time the user asks you to onboard a participant into the hackclaw Discord — sending them an invite link that, when clicked, automatically gives them the **Participants** role plus their **team role**, and unlocks their team-only private channel.

Typical prompts that should trigger this skill:

- "send an invite to X with team Y"
- "onboard Alice to Team Sample with her email alice@…"
- "send a discord link to bob@… for Team Rocket"
- "email the participants their invite links" (batch mode — use `run_batch.py`, see below)
- Any time you've just gotten a participant's name, email, and team name and they need to join the server

## The one rule that broke us before

**The running bot reads the invite→role mapping from `/opt/hackclaw/logs/invite-role-map.json`. The `InviteTracker` now reloads this file on every member join (fix commit `960adbb`), so rollout scripts that write straight to the file take effect immediately — no bot restart needed.**

But that fix only works *forward*. If a participant clicks their invite **before the mapping hits disk**, the tracker has nothing to match against, and the member joins with no roles. Order matters:

1. Role created  →
2. Channel created with role overwrite  →
3. Invite generated  →
4. **Mapping written to disk**  →
5. **Email sent** ← only after step 4.

If you reverse 4 and 5 (email first, then write mapping), a fast clicker gets no roles. Always write the mapping first.

## Required inputs

Every invocation needs three things. Ask the user if any are missing:

| Input         | Example                          | Notes |
|---------------|----------------------------------|-------|
| Participant name | `Shreyas`                     | Used in the email greeting |
| Email address    | `ssk16@illinois.edu`          | Single recipient per invite |
| Team name        | `Team Shreyas`                | Prefix with "Team " if the user didn't |

If the user provides multiple (participant, email, team) tuples in one message, run the batch flow (`scripts/run_batch.py`).

## How it works (under the hood)

The script at `scripts/stand_up_team.py` (inside this skill folder) is the single source of truth. It:

1. Loads the Discord bot token from `/opt/hackclaw/gateway-env.conf` (`DISCORD_BOT_TOKEN`).
2. Hits the Discord REST API to:
   - Create the team role (`Team <name>`) if it doesn't exist (idempotent by role name).
   - Create a private text channel (`team-<slug>`) under the Teams category (`1493806352139817172`) with @everyone denied view, and the new role + bot allowed. Idempotent by channel name — if it exists, just refreshes permission overwrites.
   - Create a new 1-use unique invite on that channel.
3. Appends `{invite_code: [participants_role_id, team_role_id]}` to `/opt/hackclaw/logs/invite-role-map.json` (preserving existing mappings).
4. Loads SMTP creds from `/root/.nanobot/config.json` (`channels.email`) and sends the participant a multipart email (HTML + plain text) with the invite link.

When the participant clicks, `on_member_join_assign_role` in `nanobot/channels/invite_tracker.py`:
- Reloads the mapping file from disk.
- Diffs invite usage counts (or detects "disappeared" 1-use invites) to identify which code was consumed.
- Adds both mapped roles to the new member in a single API call.

## How to invoke

### Single participant (most common)

```bash
scp -i ~/.ssh/hackclaw_hetzner \
  .claude/skills/send-team-invite/scripts/stand_up_team.py \
  root@46.225.155.25:/tmp/stand_up_team.py

ssh -i ~/.ssh/hackclaw_hetzner root@46.225.155.25 \
  "python3 /tmp/stand_up_team.py \
    --name 'Shreyas' \
    --email ssk16@illinois.edu \
    --team 'Team Shreyas' \
  && rm /tmp/stand_up_team.py"
```

### Batch (multiple participants)

Drop a CSV with columns `name,email,team` anywhere on your Mac, then:

```bash
scp -i ~/.ssh/hackclaw_hetzner \
  .claude/skills/send-team-invite/scripts/stand_up_team.py \
  .claude/skills/send-team-invite/scripts/run_batch.py \
  roster.csv \
  root@46.225.155.25:/tmp/

ssh -i ~/.ssh/hackclaw_hetzner root@46.225.155.25 \
  "python3 /tmp/run_batch.py /tmp/roster.csv && \
   rm /tmp/stand_up_team.py /tmp/run_batch.py /tmp/roster.csv"
```

`run_batch.py` sleeps briefly between participants to stay well under Discord's REST rate limits (50 req/s burst, but we're well under).

## Idempotency & safety

- Re-running with the same team name **does not** duplicate the role or channel — they're reused.
- Each invocation always mints a **new 1-use invite**, so sending the "same" invite to two emails is intentional only when you explicitly want both to work (in which case send each email a separately-generated invite).
- The mapping file is append-only per invite code — removing an old code requires editing the JSON manually.
- Never commit the scripts' output. The invite-role map lives at `/opt/hackclaw/logs/invite-role-map.json` and must stay on the box.

## Verification checklist (do this every time)

After running, verify the participant actually gets their roles on join:

1. Check gateway log on the Hetzner box: `ssh … "tail -n 50 /opt/hackclaw/logs/gateway.log | grep -iE 'invite|role'"`
2. Look for `Auto-role assigned: member=<name> (<id>) roles=['Participants', 'Team X']`.
3. If you see `Member joined but invite source couldn't be identified`, that's the old bug — shouldn't happen post-commit `960adbb`, but manually grant the roles via the Discord API as a safety net:
   ```bash
   ssh … "python3 -c 'import urllib.request; …PUT /guilds/{GUILD}/members/{USER}/roles/{ROLE}…'"
   ```

## What to check if something breaks

- **403 Forbidden when listing invites**: the bot needs the "Manage Server" permission on its role. Verify in Server Settings → Roles → hackclaw.
- **Invite-create REST call 401**: the bot token env var is wrong. The token lives in `/opt/hackclaw/gateway-env.conf` under `DISCORD_BOT_TOKEN`, NOT `DISCORD_TOKEN`.
- **Cloudflare 1010 from the Hetzner box**: you're missing the `User-Agent: DiscordBot (...)` header. Always set it.
- **Participant joined but no roles**: run `tail -n 50 /opt/hackclaw/logs/gateway.log | grep invite_tracker` to see the reason. Most common failure was "invite source couldn't be identified" — fixed by always reloading the mapping from disk on join.

## Do NOT

- ❌ Send the invite email before the mapping is written to disk (step 4 before step 5).
- ❌ Create the invite from a random channel — use the team's own private channel so the invite points to a channel the participant will be able to see after their role is granted.
- ❌ Set `max_uses: 0` unless you genuinely want the link to be forwardable. Default is 1-use for one-person assignment.
- ❌ Commit anything under `/opt/hackclaw/logs/` — it contains invite-role maps and log lines.
- ❌ Skip the verification checklist on batch runs — spot-check at least 2 random entries.

## References

- Bot code: `nanobot/channels/invite_tracker.py`
- Wiring: `nanobot/channels/discord.py` (`on_member_join` handler)
- Past incidents logged in `learnings.md` under "invite-tracker"
- Discord Teams category id: `1493806352139817172`
- Participants role id: `1482562237460910110`
