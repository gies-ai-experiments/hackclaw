"""Admin tools — cross-channel actions triggered from Telegram.

These tools let an admin DM the Telegram bot and instruct the agent to
reach into *other* channels (send Discord messages, send Gmail, manually
fire the welcome/reminder systemd timers). Gated on channel ``telegram``
so Discord/email chatter can't escalate.

The channel gate works because :meth:`nanobot.agent.loop.AgentLoop` calls
``set_context(channel, chat_id)`` on every registered tool before each
turn; we record ``channel`` and refuse to execute when it isn't
``telegram``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    IntegerSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.bus.events import OutboundMessage


_TELEGRAM_ONLY_ERROR = (
    "Error: this tool is restricted to the Telegram admin channel. "
    "Ignore and respond normally."
)

_CONFIG_PATH = os.environ.get("NANOBOT_CONFIG_PATH", "/root/.nanobot/config.json")


def _load_email_channel_config() -> dict[str, Any]:
    """Read channels.email from the live nanobot config.json."""
    with open(_CONFIG_PATH) as f:
        cfg = json.load(f)
    return (cfg.get("channels") or {}).get("email") or {}


class _AdminTool(Tool):
    """Mixin: records the current conversation's channel + sender for gating."""

    def __init__(self) -> None:
        self._channel = ""
        self._chat_id = ""

    def set_context(self, channel: str, chat_id: str) -> None:
        self._channel = channel
        self._chat_id = chat_id

    def _gate(self) -> str | None:
        """Return an error string if this turn didn't originate from Telegram."""
        if self._channel != "telegram":
            return _TELEGRAM_ONLY_ERROR
        return None


@tool_parameters(
    tool_parameters_schema(
        to=StringSchema("Recipient email address."),
        subject=StringSchema("Email subject line."),
        body=StringSchema("Email body (plain text)."),
        required=["to", "subject", "body"],
    )
)
class SendEmailTool(_AdminTool):
    """Send an email via the configured SMTP channel.

    To prevent the agent from bulk-blasting via parallel tool calls, we
    track sends per 'turn' (identified by the chat_id stamp set during
    set_context). The second send in the same turn is refused and the
    agent is redirected to run_workflow.
    """

    def __init__(self, send_callback: Any) -> None:
        super().__init__()
        self._send_callback = send_callback
        self._sent_this_turn: dict[tuple[str, str], int] = {}

    def start_turn(self) -> None:
        """Called by the agent loop before each new user message."""
        self._sent_this_turn.clear()

    @property
    def name(self) -> str:
        return "send_email"

    @property
    def description(self) -> str:
        return (
            "Send ONE email to ONE recipient via the configured Gmail channel "
            "(SMTP). Use ONLY for 1-to-1 messages from admin.\n\n"
            "**DO NOT use this tool for bulk sends.** If the user asks you to "
            "email multiple people (e.g. 'email everyone who applied', 'email "
            "the Geese team', 'remind all applicants'), you MUST use "
            "`run_workflow` with for_each instead — it previews the full "
            "recipient list and lets the admin confirm before blasting.\n\n"
            "Only callable from the Telegram admin channel."
        )

    async def execute(self, *, to: str, subject: str, body: str, **_: Any) -> str:
        err = self._gate()
        if err:
            return err

        # Per-turn bulk-blast guard. If the agent tries to call send_email
        # multiple times in the same turn (e.g. looping over applicants), we
        # hard-refuse after the first and tell it to use run_workflow.
        turn_key = (self._channel, self._chat_id)
        count = self._sent_this_turn.get(turn_key, 0)
        if count >= 1:
            self._sent_this_turn[turn_key] = count + 1
            return (
                "Error: send_email already fired once this turn. For multiple "
                "recipients, use run_workflow with for_each — it previews the "
                "whole batch and requires admin confirmation. Do NOT loop "
                "send_email directly."
            )
        self._sent_this_turn[turn_key] = count + 1

        msg = OutboundMessage(
            channel="email",
            chat_id=to.strip(),
            content=body,
            metadata={"subject": subject, "force_send": True},
        )
        try:
            await self._send_callback(msg)
        except Exception as exc:
            return f"Error sending email to {to}: {exc}"
        return f"Email dispatched to {to} (subject: {subject!r})"


@tool_parameters(
    tool_parameters_schema(
        to=StringSchema("Recipient email address."),
        subject=StringSchema("Email subject line."),
        body=StringSchema("Email body (plain text)."),
        required=["to", "subject", "body"],
    )
)
class DraftEmailTool(_AdminTool):
    """Save an email as a Gmail Draft via IMAP APPEND — does NOT send.

    The admin can then open gmail.com → Drafts, review/edit, and send
    manually. Useful for ghost-writing by the agent without the agent
    ever touching SMTP.
    """

    @property
    def name(self) -> str:
        return "draft_email"

    @property
    def description(self) -> str:
        return (
            "Save an email as a draft in the giesbuildathon@gmail.com Gmail "
            "Drafts folder. The email is NOT sent — the admin opens Gmail "
            "and chooses whether to review, edit, and send. Use this when "
            "the admin asks you to 'draft an email', 'make a draft', "
            "'save a draft', 'prepare a draft in gmail', or when they "
            "want to review and send themselves rather than have the bot "
            "send immediately. Telegram-only."
        )

    async def execute(self, *, to: str, subject: str, body: str, **_: Any) -> str:
        err = self._gate()
        if err:
            return err

        import imaplib
        import ssl
        import time
        from email.message import EmailMessage
        from email.utils import formatdate, make_msgid

        try:
            cfg = _load_email_channel_config()
        except Exception as exc:
            return f"Error loading email config: {exc}"

        imap_host = cfg.get("imapHost") or cfg.get("imap_host") or "imap.gmail.com"
        imap_port = int(cfg.get("imapPort") or cfg.get("imap_port") or 993)
        imap_username = cfg.get("imapUsername") or cfg.get("imap_username") or ""
        imap_password = cfg.get("imapPassword") or cfg.get("imap_password") or ""
        from_address = cfg.get("fromAddress") or cfg.get("from_address") or imap_username

        if not (imap_host and imap_username and imap_password):
            return "Error: IMAP credentials not configured in channels.email"

        msg = EmailMessage()
        msg["From"] = from_address
        msg["To"] = to.strip()
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=from_address.split("@", 1)[-1] if "@" in from_address else "local")
        msg.set_content(body)

        def _append() -> str:
            with imaplib.IMAP4_SSL(imap_host, imap_port, ssl_context=ssl.create_default_context()) as m:
                m.login(imap_username, imap_password)
                # Gmail's drafts folder. Case-sensitive; localized installs may
                # differ but for English Gmail this is stable.
                folder = "[Gmail]/Drafts"
                flags = "(\\Draft)"
                date_time = imaplib.Time2Internaldate(time.time())
                typ, data = m.append(folder, flags, date_time, msg.as_bytes())
                return f"{typ} {data!r}"

        try:
            await asyncio.to_thread(_append)
        except Exception as exc:
            return f"Error saving draft: {exc}"

        logger.info("Draft saved for {}: {!r}", to, subject)
        return (
            f"Draft saved to Gmail Drafts folder (giesbuildathon@gmail.com). "
            f"To: {to}, Subject: {subject!r}. Open Gmail -> Drafts to review "
            "and send."
        )


@tool_parameters(
    tool_parameters_schema(
        channel_id=StringSchema(
            "Numeric Discord channel ID (e.g. '1493676064239652884'). "
            "Ask the user for this ID if you don't know it."
        ),
        content=StringSchema("Message content (plaintext, <=2000 chars)."),
        required=["channel_id", "content"],
    )
)
class SendDiscordTool(_AdminTool):
    """Post a message into a Discord channel on the connected guild."""

    def __init__(self, send_callback: Any) -> None:
        super().__init__()
        self._send_callback = send_callback

    @property
    def name(self) -> str:
        return "send_discord"

    @property
    def description(self) -> str:
        return (
            "Post a message into a specific Discord channel by numeric ID. "
            "Only callable from the Telegram admin channel."
        )

    async def execute(self, *, channel_id: str, content: str, **_: Any) -> str:
        err = self._gate()
        if err:
            return err
        msg = OutboundMessage(
            channel="discord",
            chat_id=channel_id.strip(),
            content=content,
        )
        try:
            await self._send_callback(msg)
        except Exception as exc:
            return f"Error posting to Discord channel {channel_id}: {exc}"
        return f"Posted to Discord channel {channel_id}"


@tool_parameters(
    tool_parameters_schema(
        which=StringSchema(
            "Which cycle to trigger: 'welcome' (hourly first-touch) or "
            "'reminder' (6h apply-reminder).",
            enum=["welcome", "reminder"],
        ),
        required=["which"],
    )
)
class TriggerCycleTool(_AdminTool):
    """Manually fire one of the scheduled systemd services now (off-cycle)."""

    @property
    def name(self) -> str:
        return "trigger_cycle"

    @property
    def description(self) -> str:
        return (
            "Manually trigger one of the reminder systemd timers now, "
            "instead of waiting for the next scheduled fire. "
            "'welcome' = hackclaw-welcome.service (welcome new interest-form signups). "
            "'reminder' = hackclaw-reminder.service (apply-reminder for Gies who haven't applied). "
            "Only callable from the Telegram admin channel."
        )

    async def execute(self, *, which: str, **_: Any) -> str:
        err = self._gate()
        if err:
            return err
        service = {"welcome": "hackclaw-welcome.service", "reminder": "hackclaw-reminder.service"}.get(
            which.strip().lower()
        )
        if service is None:
            return f"Error: unknown cycle {which!r}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "start", service,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err_out = await proc.communicate()
            if proc.returncode != 0:
                return f"Error starting {service}: {err_out.decode().strip()}"
        except Exception as exc:
            return f"Error triggering {service}: {exc}"
        logger.info("Admin triggered {} off-cycle", service)
        return f"Triggered {service}. Check logs/{which}.log in ~15s for result."


@tool_parameters(
    tool_parameters_schema(
        gies_only=BooleanSchema(
            description="When true (default), count only Gies-eligible applicants (matches the list_applicants filter).",
            default=True,
        ),
    )
)
class CountApplicantsTool(_AdminTool):
    """Return the numeric count of applicants — LLM-safe for 'how many' questions.

    LLMs are unreliable at counting long JSON arrays. This tool returns
    a short string containing just the count, so the agent never has to
    mentally count a list it got back from ``list_applicants``.
    """

    @property
    def name(self) -> str:
        return "count_applicants"

    @property
    def description(self) -> str:
        return (
            "Return the number of unique applicants on the application sheet as "
            "a short sentence (e.g. '87 Gies-eligible applicants'). **Use this "
            "tool instead of list_applicants whenever the admin asks 'how "
            "many' or any count question.** Do NOT try to count a JSON list "
            "yourself — LLMs get it wrong. Telegram-only, read-only."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, *, gies_only: bool = True, **_: Any) -> str:
        err = self._gate()
        if err:
            return err

        sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        app_id = os.environ.get("GIES_SHEET_ID", "")
        if not sa or not app_id:
            return "Error: GOOGLE_SERVICE_ACCOUNT_JSON or GIES_SHEET_ID not configured"

        from nanobot.onboard.email_canonical import canonical_illinois_email
        from nanobot.onboard.parser import extract_members, is_gies_program
        from nanobot.onboard.sheet_io import fetch_rows, open_client, open_first_worksheet

        try:
            client = open_client(sa)
            ws = open_first_worksheet(client, app_id)
            rows = fetch_rows(ws)
        except Exception as exc:
            return f"Error reading application sheet: {exc}"

        count = 0
        seen: set[str] = set()
        for r in rows:
            trimmed = r[1:] if r else r
            for m in extract_members(trimmed):
                canon = canonical_illinois_email(m.email)
                if not canon or canon in seen:
                    continue
                seen.add(canon)
                if gies_only and not is_gies_program(m.program):
                    continue
                count += 1

        label = "Gies-eligible applicant" if gies_only else "applicant"
        return f"{count} {label}{'s' if count != 1 else ''} on the application sheet right now."


@tool_parameters(
    tool_parameters_schema(
        limit=IntegerSchema(
            0,
            description="Max members to return (0 = all). Use small values while drafting to keep tokens low.",
            minimum=0,
        ),
        gies_only=BooleanSchema(
            description=(
                "When true (default), only Gies-eligible applicants are returned "
                "— those whose declared Program / Major matches a Gies keyword "
                "(Finance, Accounting, BADM, Marketing, MBA, etc.). Set false "
                "ONLY if you specifically need the non-Gies rows too."
            ),
            default=True,
        ),
    )
)
class ListApplicantsTool(_AdminTool):
    """Read the Gies application-form sheet and return every unique applicant."""

    @property
    def name(self) -> str:
        return "list_applicants"

    @property
    def description(self) -> str:
        return (
            "Read the Gies AI for Impact Challenge application sheet and return "
            "a JSON list of unique applicants as [{name, email, team, program}]. "
            "By default, ONLY Gies-eligible applicants are returned "
            "(gies_only=true). Set gies_only=false to also include non-Gies rows. "
            "Every person listed on any team counts as an applicant; emails are "
            "canonicalized to @illinois.edu and duplicates dropped. "
            "Only callable from the Telegram admin channel."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, *, limit: int = 0, gies_only: bool = True, **_: Any) -> str:
        err = self._gate()
        if err:
            return err

        sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        app_id = os.environ.get("GIES_SHEET_ID", "")
        if not sa or not app_id:
            return "Error: GOOGLE_SERVICE_ACCOUNT_JSON or GIES_SHEET_ID not configured"

        # Imported lazily so module import is cheap when not running.
        from nanobot.onboard.email_canonical import canonical_illinois_email
        from nanobot.onboard.parser import extract_members, is_gies_program
        from nanobot.onboard.sheet_io import fetch_rows, open_client, open_first_worksheet

        try:
            client = open_client(sa)
            ws = open_first_worksheet(client, app_id)
            rows = fetch_rows(ws)
        except Exception as exc:
            return f"Error reading application sheet: {exc}"

        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for r in rows:
            team = r[1] if len(r) > 1 else ""
            trimmed = r[1:] if r else r  # drop Timestamp col before extract_members
            for m in extract_members(trimmed):
                canon = canonical_illinois_email(m.email)
                if not canon or canon in seen:
                    continue
                if gies_only and not is_gies_program(m.program):
                    continue
                seen.add(canon)
                out.append({
                    "name": m.name,
                    "email": canon,
                    "team": team,
                    "program": m.program,
                })
                if limit and len(out) >= limit:
                    return json.dumps(out)
        return json.dumps(out)


_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_.\[\]]*)\}")


def _resolve_path(path: str, ctx: dict[str, Any]) -> Any:
    """Walk dotted path like ``item.email`` or ``applicants[0].name`` in *ctx*."""
    # Split on dots but also respect [N] index steps.
    parts: list[str | int] = []
    for chunk in path.split("."):
        m = re.match(r"^([^\[]+)((?:\[\d+\])*)$", chunk)
        if not m:
            return None
        parts.append(m.group(1))
        for idx in re.findall(r"\[(\d+)\]", m.group(2)):
            parts.append(int(idx))

    val: Any = ctx
    for p in parts:
        if isinstance(p, int):
            if isinstance(val, list) and 0 <= p < len(val):
                val = val[p]
            else:
                return None
        else:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                return None
            if val is None:
                return None
    return val


def _substitute(obj: Any, ctx: dict[str, Any]) -> Any:
    """Recursively replace ``{var.path}`` in strings using *ctx*."""
    if isinstance(obj, str):
        def repl(m: re.Match[str]) -> str:
            v = _resolve_path(m.group(1), ctx)
            return str(v) if v is not None else m.group(0)
        return _VAR_RE.sub(repl, obj)
    if isinstance(obj, dict):
        return {k: _substitute(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(x, ctx) for x in obj]
    return obj


@tool_parameters(
    tool_parameters_schema(
        plan=ArraySchema(
            ObjectSchema(
                {
                    "tool": StringSchema("Name of a registered tool to call."),
                    "args": ObjectSchema({}, description="Tool args. Supports {var.path} substitution."),
                    "id": StringSchema("Optional: save the result under this name for later steps."),
                    "for_each": StringSchema("Optional: name of a saved list; run once per item with {item.*} bound."),
                },
                required=["tool"],
            ),
            description="Ordered list of steps. Each step calls a registered tool.",
        ),
        dry_run=BooleanSchema(
            description="When true, don't invoke tools; return what would happen. Default true for safety.",
            default=True,
        ),
        required=["plan"],
    )
)
class RunWorkflowTool(_AdminTool):
    """Compose calls across existing tools: list_applicants → for_each → send_email, etc.

    **Always call with dry_run=true first**, show the preview to the admin,
    then re-call with dry_run=false after they confirm.
    """

    def __init__(self, registry: Any) -> None:
        super().__init__()
        self._registry = registry

    @property
    def name(self) -> str:
        return "run_workflow"

    @property
    def description(self) -> str:
        return (
            "Execute a multi-step plan composed of other registered tools.\n\n"
            "**USE THIS for ANY multi-recipient or bulk operation** — "
            "'email all applicants', 'email the X team', 'post to all channels', "
            "etc. Do NOT iterate send_email or send_discord yourself; put them "
            "in a run_workflow plan with for_each.\n\n"
            "Plan format: a list of steps. Each step is "
            "{tool, args, id?, for_each?}. Use 'id' to save a result for later "
            "steps; use 'for_each': <saved-list-id> to loop. Inside args, "
            "'{item.email}' is replaced per-iteration and '{saved_id.field}' "
            "pulls from earlier steps.\n\n"
            "**MANDATORY two-step protocol:**\n"
            "  1. Call with dry_run=true — the tool will read sheets etc. and "
            "show the full recipient list + draft body without sending.\n"
            "  2. Relay the preview back to the admin (count, draft, sample).\n"
            "  3. WAIT for the admin to reply 'send', 'go', 'yes', or "
            "equivalent explicit confirmation.\n"
            "  4. Only then call with dry_run=false using the same plan.\n\n"
            "Telegram-only."
        )

    async def execute(self, *, plan: list[dict[str, Any]], dry_run: bool = True, **_: Any) -> str:
        err = self._gate()
        if err:
            return err
        if not isinstance(plan, list) or not plan:
            return "Error: plan must be a non-empty list of steps"

        variables: dict[str, Any] = {}
        lines: list[str] = [f"Workflow ({'DRY-RUN' if dry_run else 'LIVE'}) — {len(plan)} step(s):"]

        for idx, step in enumerate(plan, start=1):
            tool_name = step.get("tool")
            if not tool_name:
                return f"Error: step {idx} missing 'tool'"
            if tool_name == "run_workflow":
                return f"Error: step {idx} cannot call run_workflow (no recursion)"

            args = step.get("args", {}) or {}
            for_each_key = step.get("for_each")
            save_as = step.get("id")

            # Read-only tools are safe during dry-run (and required, so downstream
            # for_each loops have real data to iterate over).
            tool_obj = None
            if hasattr(self._registry, "get"):
                tool_obj = self._registry.get(tool_name)
            is_readonly = bool(tool_obj and getattr(tool_obj, "read_only", False))
            should_execute = (not dry_run) or is_readonly

            if for_each_key:
                items = variables.get(for_each_key)
                if not isinstance(items, list):
                    return (
                        f"Error: step {idx} for_each='{for_each_key}' is not a saved list. "
                        f"Available: {sorted(variables.keys())}"
                    )
                lines.append(f"  step {idx}: {tool_name} × {len(items)} (for_each={for_each_key})")
                preview_shown = 0
                failures = 0
                for item in items:
                    resolved = _substitute(args, {**variables, "item": item})
                    if not should_execute:
                        if preview_shown < 3:
                            lines.append(f"    would call {tool_name}({resolved})")
                            preview_shown += 1
                    else:
                        out = await self._registry.execute(tool_name, resolved)
                        if isinstance(out, str) and out.startswith("Error"):
                            failures += 1
                if not should_execute and len(items) > 3:
                    lines.append(f"    … {len(items) - 3} more")
                if should_execute:
                    lines.append(f"    sent={len(items) - failures}, failed={failures}")

            else:
                resolved = _substitute(args, variables)
                if not should_execute:
                    lines.append(f"  step {idx}: would call {tool_name}({resolved})")
                    if save_as:
                        lines.append(f"    (would save as {save_as!r})")
                else:
                    out = await self._registry.execute(tool_name, resolved)
                    if save_as:
                        # Best effort: if the tool returned JSON, parse it so for_each works.
                        parsed: Any = out
                        if isinstance(out, str):
                            try:
                                parsed = json.loads(out)
                            except (json.JSONDecodeError, ValueError):
                                parsed = out
                        variables[save_as] = parsed
                    summary = out if isinstance(out, str) else json.dumps(out)[:200]
                    prefix = "  step {}: ".format(idx)
                    if dry_run and is_readonly:
                        prefix += "(read-only, executed in dry-run) "
                    lines.append(f"{prefix}{tool_name} → {summary[:200]}")

        if dry_run:
            lines.append(
                "\nThis was a DRY-RUN. Nothing mutating was sent. "
                "Re-call run_workflow with the same plan and dry_run=false to execute."
            )
        return "\n".join(lines)
