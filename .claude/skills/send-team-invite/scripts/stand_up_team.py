"""Stand up a team role + private team channel + 1-use invite, write mapping,
and email the participant their Discord invite link.

Runs on the Hetzner box (has the config + bot token on disk). Safe to
re-run: existing roles/channels are reused, a fresh 1-use invite is
always minted.

Hard rule: mapping is written to /opt/hackclaw/logs/invite-role-map.json
BEFORE the email is sent, so a fast clicker can't beat the role assign.
"""
from __future__ import annotations

import argparse
import json
import re
import smtplib
import ssl
import urllib.request
import urllib.error
from email.message import EmailMessage
from pathlib import Path


# --- constants (Gies buildathon guild) ---
GUILD = "1482448643117154567"
TEAMS_CATEGORY = "1493806352139817172"
BOT_ID = "1491346305765871676"
PARTICIPANTS_ROLE = "1482562237460910110"
MAP_PATH = Path("/opt/hackclaw/logs/invite-role-map.json")
ENV_CONF = Path("/opt/hackclaw/gateway-env.conf")
NANOBOT_CONF = Path("/root/.nanobot/config.json")


def _token() -> str:
    for line in ENV_CONF.read_text().splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"DISCORD_BOT_TOKEN not found in {ENV_CONF}")


def _headers(tok: str) -> dict[str, str]:
    return {
        "Authorization": f"Bot {tok}",
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (https://github.com/gies-ai-experiments/hackclaw, 0.1)",
    }


def _api(hdr: dict[str, str], method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"https://discord.com/api/v10{path}", data=data, method=method, headers=hdr,
    )
    try:
        raw = urllib.request.urlopen(req).read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Discord API error: {method} {path} -> {e.code} {e.read().decode(errors='replace')[:400]}"
        ) from e


def _slugify(name: str) -> str:
    """Team name -> channel-safe slug. 'Team Cache Me Outside!' -> 'team-cache-me-outside'."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not s.startswith("team-"):
        s = f"team-{s}"
    return s[:90]  # Discord channel name limit is 100


def stand_up_team(*, name: str, email: str, team: str, dry_run: bool = False) -> dict:
    """End-to-end: role + channel + invite + mapping + email. Returns result dict."""
    team = team.strip()
    if not team.lower().startswith("team "):
        team = f"Team {team}"
    channel_name = _slugify(team)

    hdr = _headers(_token())

    # --- role (idempotent by name) ---
    existing_roles = {r["name"]: r["id"] for r in _api(hdr, "GET", f"/guilds/{GUILD}/roles")}
    if team in existing_roles:
        role_id = existing_roles[team]
        role_status = "exists"
    else:
        if dry_run:
            role_id = "<would-create>"
            role_status = "would-create"
        else:
            role = _api(
                hdr, "POST", f"/guilds/{GUILD}/roles",
                {"name": team, "mentionable": True, "hoist": False},
            )
            role_id = role["id"]
            role_status = "created"

    # --- channel (idempotent by name, perms refreshed on every run) ---
    VIEW = 1024
    SEND = 2048
    READ_HIST = 65536
    EMBED = 16384
    ATTACH = 32768
    REACT = 64
    EXTERN = 1 << 18
    ALLOW = VIEW | SEND | EMBED | ATTACH | READ_HIST | REACT | EXTERN

    overwrites = [
        {"id": GUILD,   "type": 0, "allow": "0", "deny": str(VIEW)},
        {"id": role_id, "type": 0, "allow": str(ALLOW), "deny": "0"},
        {"id": BOT_ID,  "type": 1, "allow": str(ALLOW), "deny": "0"},
    ]

    chs = _api(hdr, "GET", f"/guilds/{GUILD}/channels")
    existing_ch = next((c for c in chs if c["name"] == channel_name), None)
    if existing_ch:
        ch_id = existing_ch["id"]
        if not dry_run:
            _api(hdr, "PATCH", f"/channels/{ch_id}", {"permission_overwrites": overwrites})
        ch_status = "exists"
    else:
        if dry_run:
            ch_id = "<would-create>"
            ch_status = "would-create"
        else:
            ch = _api(
                hdr, "POST", f"/guilds/{GUILD}/channels",
                {
                    "name": channel_name,
                    "type": 0,
                    "parent_id": TEAMS_CATEGORY,
                    "permission_overwrites": overwrites,
                },
            )
            ch_id = ch["id"]
            ch_status = "created"

    # --- invite (always fresh) ---
    if dry_run:
        invite_url = "https://discord.gg/<would-create>"
        invite_code = "<would-create>"
    else:
        inv = _api(
            hdr, "POST", f"/channels/{ch_id}/invites",
            {"max_age": 0, "max_uses": 1, "unique": True, "temporary": False},
        )
        invite_code = inv["code"]
        invite_url = f"https://discord.gg/{invite_code}"

    # --- mapping (write BEFORE email) ---
    if not dry_run:
        MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        m = json.loads(MAP_PATH.read_text()) if MAP_PATH.exists() else {}
        m[invite_code] = [PARTICIPANTS_ROLE, role_id]
        MAP_PATH.write_text(json.dumps(m, indent=2, sort_keys=True))

    # --- email ---
    if not dry_run:
        cfg = json.loads(NANOBOT_CONF.read_text())["channels"]["email"]
        first = name.strip().split()[0] if name.strip() else "there"

        subject = f"Your Discord invite — {team} — Gies AI for Impact Challenge"
        text = (
            f"Hi {first},\n\n"
            f"You're invited to the Gies AI for Impact Challenge Discord server.\n"
            f"Your team is {team}.\n\n"
            f"Click to join (1-use link, just for you): {invite_url}\n\n"
            f"When you join you'll automatically get the Participants role plus the "
            f"{team} role, which unlocks your private team channel (#{channel_name}) "
            "— only you and your teammates can see or post in it.\n\n"
            "— The Gies Buildathon team\n"
        )
        html = f"""<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #1f2937;">
  <p>Hi {first},</p>
  <p>You're invited to the <strong>Gies AI for Impact Challenge</strong> Discord server.<br>
     Your team is <strong>{team}</strong>.</p>
  <p>
    <a href="{invite_url}" style="display:inline-block;background:#5865F2;color:#fff;
       padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:600;">
      Join the Discord
    </a>
  </p>
  <p style="color:#6b7280;font-size:13px;">
    1-use link, just for you: <a href="{invite_url}">{invite_url}</a>
  </p>
  <p>When you join you'll automatically get the <strong>Participants</strong> role plus the
     <strong>{team}</strong> role, which unlocks your private team channel
     (<code>#{channel_name}</code>) — only you and your teammates can see or post in it.</p>
  <p>— The Gies Buildathon team</p>
</body>
</html>
"""

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg["fromAddress"]
        msg["To"] = email
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")

        ctx = ssl.create_default_context()
        with smtplib.SMTP(cfg["smtpHost"], int(cfg["smtpPort"])) as s:
            s.ehlo()
            if cfg.get("smtpUseTls", True):
                s.starttls(context=ctx)
                s.ehlo()
            s.login(cfg["smtpUsername"], cfg["smtpPassword"])
            s.send_message(msg)

    return {
        "name": name,
        "email": email,
        "team": team,
        "role_id": role_id,
        "role_status": role_status,
        "channel_id": ch_id,
        "channel_name": channel_name,
        "channel_status": ch_status,
        "invite_code": invite_code,
        "invite_url": invite_url,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="Participant's name (for email greeting)")
    ap.add_argument("--email", required=True, help="Participant's email address")
    ap.add_argument("--team", required=True, help="Team name (e.g. 'Team Sample')")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without creating/emailing anything",
    )
    args = ap.parse_args()

    result = stand_up_team(
        name=args.name, email=args.email, team=args.team, dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
