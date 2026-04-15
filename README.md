<div align="center">
  <h1>hackclaw</h1>
  <p>AI-powered hackathon assistant for the Gies AI for Impact Challenge</p>
</div>

## What is hackclaw?

**hackclaw** is a Discord + Email bot built for the **Gies AI for Impact Challenge** — a 24-hour hackathon at the University of Illinois where students build AI agents using no-code/low-code tools.

It answers participant questions, manages a help ticket queue for technical mentors, and provides a dedicated Copilot Studio knowledge channel.

## Features

### Brain-Routed Knowledge Base
hackclaw's knowledge is organized into topic-specific brain files (schedule, rules, tracks, judging, etc.). When a question comes in, the bot reads the index, identifies the relevant section, and loads only the targeted content to answer accurately.

### Help Ticket Queue
Participants request help via `/helpme` — the bot posts to `#help-queue` with Claim/Unclaim/Resolve buttons for mentors.

- **Auto-suggest past solutions** — When a new ticket is raised, hackclaw checks past resolved tickets using embedding similarity. If a similar issue was solved before, it suggests the solution before creating a ticket.
- **Interactive flow** — Users can try the suggestion first. If it doesn't help, the ticket is created automatically without re-typing.
- **Resolve with solution capture** — When mentors resolve tickets, a modal asks "How did you solve this?" — building a knowledge base for future issues.
- **Mentor channel management** — Mentors are auto-added to team channels when they claim a ticket, and removed when resolved.
- **Reminder pings** — Unclaimed tickets get re-pinged after 10 minutes.

### Copilot Studio Expert Channel
A dedicated `#ask-hackclaw-copilot` channel answers questions exclusively from the Copilot Studio textbook — no general hackathon knowledge leaks in.

### Multi-Channel Support
- **Discord** — Slash commands, embeds, button interactions, per-channel knowledge scoping
- **Email** — Responds to participant emails

## Architecture

```
brain/                  # Knowledge base (markdown files)
  brain.md              # Index — routes questions to the right file
  schedule.md           # Event timeline
  rules.md              # Competition rules
  copilot-studio.md     # Copilot Studio textbook
  solutions.json        # Auto-built from resolved help tickets
  ...

nanobot/                # Core bot framework
  helpqueue/            # Help ticket system
    ticket.py           # HelpTicket data model + in-memory store
    handler.py          # Slash command handlers + button interactions
    views.py            # Discord embeds, modals, button views
    solutions.py        # Solution KB with embedding similarity search
  channels/             # Discord + Email channel implementations
  agent/                # LLM agent loop, context builder, tools
  onboard/              # Team registration CSV parsing
```

## Setup

### Prerequisites
- Python 3.11+
- Discord bot token
- OpenAI API key

### Quick Start

```bash
# Install
pip install -e ".[discord]"

# Configure
cp config.example.json ~/.nanobot/config.json
# Edit config.json with your Discord token, guild ID, and channel IDs

# Set environment variables
export DISCORD_BOT_TOKEN="your-token"
export OPENAI_API_KEY="your-key"

# Run
python -m nanobot gateway
```

### Discord Bot Configuration

In `~/.nanobot/config.json`:

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "${DISCORD_BOT_TOKEN}",
      "guildId": "your-guild-id",
      "allowFrom": ["*"],
      "helpQueue": {
        "channelId": "your-help-queue-channel-id",
        "mentorRoleId": "your-mentor-role-id",
        "reminderMinutes": 10
      }
    }
  }
}
```

## Slash Commands

| Command | Description |
|---------|-------------|
| `/helpme location: problem:` | Request help from a technical mentor |
| `/resolved` | Mark your help request as resolved |

## License

MIT
