---
name: blastpost
description: MANDATORY before any event-wide announcement that fans out to multiple Discord channels OR multiple email recipients. Covers the two-step preview-then-confirm protocol, scope rules (team channels vs ask-hackclaw), and how to draft both Discord posts and Gmail emails for a single broadcast. Load whenever the admin asks to "blast", "announce to everyone", "send to all teams", "remind everybody", or anything similar.
metadata: {"nanobot":{"emoji":"📣"}}
---

# Blastpost

How to broadcast a single announcement to every team — Discord posts in
each team channel + Gmail to every participant — using the
`blast_post` tool. The protocol is the same shape as the `gmail`
skill: never fire a live blast without an explicit admin OK on this turn.

## 🚨 LOADING RULE — this skill is mandatory

You MUST have this skill loaded in context before:

- Calling `blast_post(...)` for the first time on a turn
- Telling the admin what you're about to broadcast
- Confirming after a blast was fired

## The two-step protocol — non-negotiable

**Step 1 — preview first.** When the admin asks for a blast (any phrasing
that means "tell everyone X"):

1. Draft the announcement text in your head.
2. Call `blast_post(mode="preview", subject="…", message="…")`.
3. The tool returns the rendered Discord post + a count of recipients
   (Discord channels and email addresses). It does NOT post or send
   anything.
4. Quote the preview back to the admin in Telegram. Show the exact
   subject + message body, exactly as the recipients will see it. Then
   ask: *"Reply 'send' to fire this blast, or tell me what to change."*

**Step 2 — only on explicit go.** Only call `blast_post(mode="send", ...)`
when the admin's most recent message says one of:

- "send"
- "send it"
- "go"
- "go ahead"
- "yes send"
- "blast it"

Anything else — even an enthusiastic "this looks great!" — is **not**
authorization. Re-confirm before sending.

If the admin asks for edits, redraft and re-run `mode="preview"`. Do
NOT skip the preview on the second draft.

## When you can skip preview

Only one case: the admin explicitly says *"don't preview, just send"* in
the same message that gives you the announcement text. E.g.
*"send a blast to everyone right now: submissions are extended by 30 minutes — don't preview"*.
Default to preview if there's any ambiguity.

## What `blast_post` actually does

- **Discord side:** posts the same message into every text channel
  under the Teams category (one post per team channel). Optionally
  posts in `#announcements` too. Each post starts with
  `📣 Event-wide update — ` and uses `@everyone` only when explicitly
  requested.
- **Email side:** parses the team roster (`teams.md`) and creates one
  email per participant. In `mode="preview"`: counts only. In
  `mode="send"`: drops the email into Gmail Drafts (`drafts_only=True`,
  the default) OR sends via SMTP (`drafts_only=False`).
- **Hard caps**: refuses to fan out to >60 Discord channels or >200
  emails. If the count is over either cap, the tool returns an error
  and asks the admin to reduce scope.

## Scope rules (when to broadcast where)

- **Default**: every team channel + every participant email.
- Add `include_announcements=True` to also post in `#announcements` —
  use for genuinely event-wide updates (deadline changes, room moves).
- Add `discord_only=True` for Discord-only blasts (e.g. when Gmail's
  daily quota is at risk, or the announcement is too granular to email).
- Add `email_only=True` for email-only blasts (rare — typically only
  when you want a private text trail).

## Pinging @everyone

By default, posts do NOT trigger an `@everyone` ping (the text just
appears as a regular bot message). Set `mention_everyone=True` to fire
the actual notification. Use it sparingly — overuse trains people to
mute the channel. Reserve for:

- Submission deadline (12:00 PM)
- Schedule/room changes
- Final-round assembly call

## What to write in the preview to the admin

Keep the Telegram preview tight:

```
Here's the draft (preview, nothing posted yet):

Subject: <subject>
Body:
> <line 1>
> <line 2>
> ...

Would post to N Discord team channels (+ #announcements: yes/no, @everyone: yes/no).
Would email K participants in Gmail Drafts (drafts only: yes/no).

Reply 'send' to fire it, or tell me what to change.
```

## Good announcement style

- Subject ≤ 60 chars; body ≤ 10 lines.
- Lead with WHEN the action is needed; the why comes after.
- Links as raw URLs, not markdown anchors (Discord doesn't render `<a>`).
- One topic per blast — split unrelated updates into separate calls.

## Do NOT

- Skip the preview step.
- Bundle the 1st draft and the live send into the same tool call.
- Use `mention_everyone=True` for low-priority info.
- Re-blast within 30 minutes unless the admin explicitly asks
  ("two too many notifications" is a real complaint).
- Email if Gmail's daily quota is suspected exhausted — fall back to
  `discord_only=True` and tell the admin.
