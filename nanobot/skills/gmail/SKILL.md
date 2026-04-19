---
name: gmail
description: MANDATORY before any email drafting, preview, or send via send_email or run_workflow+send_email. Covers subject/body drafting, from-address, greeting and signoff conventions, the admin-confirmation protocol (required for 1-to-1 AND bulk), and the Telegram-only gate. Load and follow this skill any time the admin so much as mentions email, remind, announce, follow-up, notify, alert, or references a recipient/list of recipients. No email action may proceed without it.
metadata: {"nanobot":{"emoji":"📧"}}
---

# Gmail

How to send email from the `giesbuildathon@gmail.com` Gmail account using
the tools already registered on the agent. This skill is triggered any
time the admin (via Telegram only) asks you to email someone.

## 🚨 LOADING RULE — this skill is mandatory

You MUST have this skill loaded in context before you do ANY of the
following, every time without exception:

- Drafting an email subject or body (even a "quick one-liner")
- Calling `send_email(...)`
- Calling `run_workflow(...)` with a plan that contains `send_email`
- Telling the admin what you're about to email
- Confirming after an email was sent

If you catch yourself about to do any of those without having read this
skill on this turn, stop and reload it first. There is no
"simple enough" exception. The confirmation protocol below is the
reason this skill exists — bypassing it is a real incident.

## 🚨 GLOBAL RULE — admin approval is mandatory for EVERY send

**You must never fire `send_email` or a live `run_workflow` without the
admin explicitly approving the exact draft first.** This applies to:

- Bulk sends (obvious) — always preview + wait for "send".
- **1-to-1 sends — ALSO ALWAYS preview + wait for confirmation.** Even
  when the admin says "email alice@illinois.edu saying hi", you do NOT
  fire the tool yet. You draft the subject + body, show the admin the
  exact copy, and wait for an explicit "send" / "go" / "yes" / "ship"
  before calling `send_email`.

The only thing you're allowed to do autonomously is **draft and
preview**. Everything that touches the SMTP pipe is human-gated.

If the admin replies with anything other than explicit confirmation
(edits, questions, "hmm", clarifications), you update the draft and
preview again. Silence or ambiguity ≠ consent.

## When to use (trigger phrases)

Activate this skill the moment the user says anything matching:

- "send an email to …"
- "email …"
- "remind … by email"
- "email everyone / all applicants / the X team"
- "follow up with … over email"
- "announce … to the applicants"

If the request isn't on the **Telegram admin channel** (channel name
returned by `set_context` will be the current channel), you do **not**
have email send authority. Say so plainly and do not attempt the tools.
The tools self-guard but you should not waste a call.

## Tools available

| Tool | Use for |
|---|---|
| `send_email(to, subject, body)` | **Exactly ONE recipient.** 1-to-1 admin replies, confirmations to a single person, etc. Hard-capped at one call per turn — you cannot loop it. |
| `list_applicants(limit=0, gies_only=true)` | Read the application-form sheet. Returns a JSON list of `{name, email, team, program}` unique applicants. `gies_only=true` by default (the challenge is Gies-only; you rarely need false). |
| `run_workflow(plan, dry_run=true)` | **The ONLY way to do bulk sends.** Chains existing tools with `for_each` iteration and `{item.*}` substitution. Mandatory two-step protocol below. |

## 1-to-1 sends — ALSO require preview + confirmation

Even when the admin names the single recipient, do NOT fire `send_email`
on the first turn. Draft it, show it, wait.

**Turn 1 — admin asks:** *"email alice@illinois.edu saying the schedule moved to 4pm"*

**Your reply (no tool call):**

```
Draft for alice@illinois.edu:

  Subject: Schedule update — Gies AI for Impact Challenge
  Body:    Hi Alice,

           Quick update — the schedule moved to 4 PM today at BIF.
           See you there,
           — Gies AI for Impact Challenge Team

Send this? Reply "send" to fire, or tell me what to change.
```

**Turn 2 — admin says "send":**

NOW call:

```
send_email(
  to="alice@illinois.edu",
  subject="Schedule update — Gies AI for Impact Challenge",
  body="Hi Alice,\n\nQuick update — the schedule moved to 4 PM today at BIF.\n\nSee you there,\n— Gies AI for Impact Challenge Team"
)
```

Then confirm with: `"Email sent to alice@illinois.edu."`

If the admin gives edits instead ("make it shorter", "add the Zoom link"),
update the draft and preview again. Repeat until they confirm.

## Bulk sends — MANDATORY confirmation flow

**Never** loop `send_email` yourself. The tool will refuse the second
call in the same turn and redirect you here.

### Step 1 — Preview (dry_run=true)

Build a `run_workflow` plan and call with `dry_run=true`:

```json
{
  "plan": [
    {"tool": "list_applicants", "id": "apps", "args": {"gies_only": true}},
    {
      "tool": "send_email",
      "for_each": "apps",
      "args": {
        "to": "{item.email}",
        "subject": "Mentor office hours tomorrow at 3 PM",
        "body": "Hi {item.name},\n\nQuick heads-up — mentor office hours are tomorrow at 3 PM at BIF.\n\n— Gies AI for Impact Challenge Team"
      }
    }
  ],
  "dry_run": true
}
```

Because `list_applicants` is read-only, the dry-run still fetches the
real applicant list and shows you exactly who would be emailed and the
rendered body for the first recipient.

### Step 2 — Relay preview to the admin

Show the admin: total recipient count, the draft subject, and the first
recipient's rendered body so they can sanity-check the personalization.

Example reply:

```
Preview — would email 87 Gies applicants.

Sample (Luke Manthuruthil):
  Subject: Mentor office hours tomorrow at 3 PM
  Body:    Hi Luke Manthuruthil,
           Quick heads-up — mentor office hours are tomorrow at 3 PM at BIF.
           — Gies AI for Impact Challenge Team

Reply "send" to proceed, or tell me what to change.
```

### Step 3 — Wait for explicit confirmation

Only proceed when the admin replies with one of: `send`, `go`, `yes`,
`proceed`, `fire`, `ship`. If they say anything else (edit the subject,
change the audience, cancel), update the plan and dry-run again.

### Step 4 — Execute (dry_run=false)

Re-call `run_workflow` with the SAME plan and `dry_run=false`. Report the
final `sent=N failed=N` back to the admin.

## Sender conventions

- **From** is always `giesbuildathon@gmail.com` (this is the SMTP account
  configured on the box; you can't change it).
- **Subject** — clear, under 80 chars, no "!!!" or emojis unless the
  admin explicitly wants them.
- **Greeting** — use `{item.name}` in bulk. If the name is blank, the
  system substitutes `"there"`.
- **Sign-off** — prefer `— Gies AI for Impact Challenge Team` unless the
  admin says otherwise.
- **Links** — the application form is
  `https://forms.gle/Az8PGE1u8rwwkFWy8`; the deadline is Apr 19, 2026
  at 11:59 PM.

## Audience shortcuts

| Admin says | Use |
|---|---|
| "all applicants" / "everyone who applied" | `list_applicants(gies_only=true)` — 87 people as of last check |
| "all applicants including non-Gies" | `list_applicants(gies_only=false)` |
| "the X team" | `list_applicants` then filter inside the plan via `for_each` + check item.team |
| "non-applicants on the interest form" | You don't have a list tool for this; the reminder-poller handles it automatically. Tell the admin "that runs on the hourly welcome cron and 6-hour reminder cron". |

## Common mistakes to avoid

1. **Don't fire `send_email` without admin approval of the exact draft.**
   Even 1-to-1 sends need a preview turn first. No exceptions.
2. **Don't call `send_email` in a loop.** The per-turn guard refuses and
   routes you here. Use `run_workflow`.
3. **Don't skip the preview.** Always `dry_run=true` first for any
   multi-recipient send, even if the admin just says "go".
4. **Don't treat "send an email to Alice saying X" as an approval to
   send.** That's a *request* to draft — you draft, show, and wait for
   a separate "send" turn.
5. **Don't use `email_channel.send()` directly.** `send_email` is the
   only blessed path because it enforces the force_send metadata and
   sets the subject correctly.
6. **Don't send from Discord or Email channels.** The tools are
   Telegram-gated and will refuse; explain the restriction and move on.

## Acknowledging the admin

After a successful bulk send, reply with a 2-line summary:

```
Sent 87 Gies applicant emails. 0 failures. Took 19 s.
```

Keep it terse — the admin can see it worked; they don't need the preview re-shown.
