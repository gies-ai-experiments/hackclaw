# Microsoft Copilot Studio — Textbook

> A complete guide for hackathon participants building AI agents with Microsoft Copilot Studio. No coding required.

---

## 1. What Is Copilot Studio?

Microsoft Copilot Studio is a **cloud-based, no-code/low-code platform** for building, launching, and managing custom AI-powered agents. It is part of the Microsoft Power Platform ecosystem.

**Key facts:**
- Build agents that automate workflows, answer questions, and interact with business systems using natural language
- Ranges from simple prompt-and-response agents to fully autonomous agents that execute entire workflows
- Your first agent can be live in **under 10 minutes**
- No data scientists or developers needed
- Enterprise-grade security built on Microsoft Azure

**What you can build:**
- Standalone agents for customer and employee scenarios
- Extensions to Microsoft 365 Copilot with custom knowledge and actions
- Autonomous agents that perform long-running operations without waiting for a prompt

### Two Tiers

| Tier | For | Included In |
|------|-----|-------------|
| **Copilot Studio Lite** | Simple internal agents (helpdesks, FAQ bots, onboarding) | Microsoft 365 Copilot license |
| **Copilot Studio Full** | External-facing agents, complex workflows, multi-agent orchestration | Separate license |

---

## 2. Getting Started — Step by Step

### Prerequisites
- Go to [copilotstudio.microsoft.com](https://copilotstudio.microsoft.com)
- A free trial lets you create and test agents (but not publish)

### Step 1: Choose Your Creation Method

| Method | Best For |
|--------|----------|
| **AI-assisted creation** | First-time users. Copilot asks questions and builds the agent from your answers |
| **Templates** | Starting from a proven pattern and customizing |
| **Manual creation** | Full control from scratch |

### Step 2: Describe Your Agent

When using AI-assisted creation, describe in natural language:
- **Purpose and target audience** — who uses it, what topics/processes it covers
- **Tone** — "summarize in bullet points," "be friendly and concise"
- **Knowledge sources** — public websites, SharePoint documents
- **Prohibited topics** — what the agent should NOT address
- Description can be up to 1,024 characters

### Step 3: AI Generates Your Agent

The AI creates the agent's name, description, instructions, and suggests triggers, channels, knowledge sources, and tools. You can accept, ignore, or dismiss suggestions.

### Step 4: Configure and Customize

- **Name, description, and instructions** (instructions up to 8,000 characters)
- **Knowledge sources** (select "+ Add knowledge")
- **Agent icon** (PNG, under 72 KB, max 192x192 px)
- **Suggested prompts** for users

### Step 5: Add Topics

Topics define conversation paths:
1. Go to the Topics page
2. Select "+ Add a topic" > "Topic" > "Add from description with Copilot"
3. Name the topic, describe what it should do, select "Create"
4. Each topic contains nodes: message, question, action, condition, etc.

### Step 6: Add Tools and Integrations

- Power Automate flows for backend actions
- 1,400+ connectors for external services
- Custom APIs and plugins

### Step 7: Test Your Agent

- Use the built-in test chat panel on the right side
- Cycle: **Test > Make changes > Test > Repeat**
- Use "Track between topics" toggle to follow conversation flow

### Step 8: Publish

1. Select "Publish" at the top of the page
2. Green banner confirms success
3. Use "Go to demo website" to share a preview URL
4. Configure channel availability (Teams, website, etc.)

---

## 3. Core Concepts

### Topics

A topic is a portion of a conversation containing one or more **nodes** (messages, questions, branches, actions).

- **System Topics** — pre-built, non-deletable (greeting, escalation, fallback, conversation end). Can be turned off.
- **Custom Topics** — user-created for specific business scenarios. Can be created from scratch or AI-generated from a description.

### Triggers

Triggers determine when a topic activates:

| Trigger Type | How It Works |
|-------------|-------------|
| **Classic triggers** | Match user input against predefined phrases (fuzzy matching) |
| **Generative orchestration** | AI-driven intent analysis; can recognize multiple intents in one message |
| **Event triggers** | Fire on external events (new email, new Dataverse row, SharePoint file created) |
| **Scheduled triggers** | Run hourly, daily, weekly, or monthly |

### Actions

Actions let agents call external services:
- Run Power Automate flows
- Call REST APIs
- Use 1,400+ connectors
- Execute agent flows (support high throughput and "human-in-the-loop")
- With generative orchestration, actions chain automatically

### Knowledge Sources

| Source Type | Details |
|------------|---------|
| **Public websites** | Bing-indexed content; scope to up to 4 specific sites |
| **SharePoint** | Sites, pages, lists (up to 15 lists); real-time for lists |
| **File upload** | Max 512 MB each; stored as vector embeddings in Dataverse |
| **Dataverse** | Up to 15 tables per source; supports unstructured reasoning |
| **Azure AI Search** | Enterprise search indexes |
| **Real-time connectors** | Live data from connected services |

**Limits:** Max 500 knowledge objects per agent, max 5 different source types at a time.

### Generative AI Capabilities

- **Generative Answers** — searches knowledge sources and generates answers on the fly, even without a scripted topic
- **Generative Orchestration** — LLM-driven planner that interprets intent, breaks down complex requests, selects tools, and executes multi-step plans
- **Auto-Prompting** — agent generates follow-up questions to fill missing info (no manual question nodes needed)
- **Web Search** — access real-time information via Bing
- **Ungrounded Responses** — optional setting allowing general knowledge responses without knowledge sources

---

## 4. Generative vs. Classic Orchestration

| Feature | Classic | Generative |
|---------|---------|------------|
| Topic selection | Trigger phrase matching | AI-driven intent analysis |
| Multi-intent | One topic at a time | Multiple intents simultaneously |
| Actions | Called explicitly from topics | Called automatically by AI planner |
| Knowledge | Fallback or explicit call | Dynamically selected |
| Conversation flow | Rule-based, predictable | Fluid, context-aware |
| Slot filling | Manual question nodes | Auto-prompting by AI |

**Recommendation:** Use generative orchestration for new projects unless you need strict predictability.

---

## 5. Publishing to Channels

### Supported Channels

- **Microsoft Teams & Microsoft 365 Copilot** — primary enterprise channel with automatic Entra ID auth
- **SharePoint** — embed agents directly in sites
- **Custom websites** — via demo link or Direct Line API
- **Power Pages** — embed in portals
- **Facebook, Slack, WhatsApp** — social/messaging channels
- **Mobile apps** — via Azure Bot Service
- **Any Azure Bot Service channel**

### Authentication Options

| Option | Use Case |
|--------|----------|
| **Authenticate with Microsoft** (default) | Teams, Power Apps, M365 Copilot |
| **Authenticate manually** | Custom OAuth configuration |
| **No authentication** | Anyone with the link can chat |

### Publishing Tips
- Every update requires hitting "Publish" to push changes live
- Updates propagate to all configured channels simultaneously
- May take a few minutes to a few hours to become available

---

## 6. Testing and Debugging

### Built-In Test Panel
- Right side of the authoring canvas
- Click any response to jump to the corresponding node
- Colored checkmarks show which nodes fired
- "Track between topics" auto-follows conversation transitions
- "Reset" clears state for a fresh test

### Developer Mode
Type `-developer on` in the test chat for deep debugging:
- Agent metadata (ID, version, conversation ID)
- Which capabilities were invoked and their status
- Downloadable diagnostic logs

### Agent Evaluation (Automated Testing)
- Create evaluation sets with predefined test questions
- Reuse Test Pane interactions as test cases
- AI-powered generation of test queries
- Test methods: **text match** (exact, partial, keyword) and **topic match**
- Supports **multi-turn** conversation testing

---

## 7. Writing Great Agent Instructions

Instructions are the **most critical element** — they control how the agent decides what to call, how to fill inputs, and how to respond.

### Rules for Good Instructions

1. **Be specific and clear** — use precise verbs: "ask," "search," "send," "check," "use"
2. **Use examples (few-shot prompting)** — show the agent what good responses look like
3. **Keep it brief** — overly long instructions cause latency or timeouts
4. **Give the agent an "out"** — define fallback behavior ("respond with 'not found' if the answer isn't present")
5. **Add guardrails** — "Only respond to messages relevant to [topic]. Otherwise, tell the user you can't help."
6. **Specify format and audience** — define tone, technicality, output format
7. **Ground in configured tools** — agents can't use tools that aren't actually connected
8. **Test incrementally** — add one instruction at a time and test between each

### Example Instruction Pattern

```
You are a Gies College HR assistant. You help employees with benefits questions,
leave policies, and onboarding procedures.

- Always respond in a friendly, professional tone
- Use bullet points for multi-step answers
- If the question is about payroll, direct the user to payroll@gies.illinois.edu
- Never discuss salary information for specific employees
- If you don't know the answer, say "I don't have that information. Please contact
  HR directly at hr@gies.illinois.edu"
```

---

## 8. Connectors and Integrations

### Power Platform Integration
- Agents trigger Power Automate flows for backend logic, approvals, cross-system operations
- Agent flows support high throughput, low latency, "human-in-the-loop"

### Dataverse Integration
- Natural language Q&A over structured/unstructured data
- Dynamic prompts grounded in enterprise data
- Requires Dataverse search enabled by admin

### Azure AI Integration
- Backed by Azure OpenAI Service
- Multiple AI models available (GPT-5, Claude, etc.)
- Application Insights for telemetry and monitoring
- Azure AI Search as knowledge source

### 1,400+ Connectors
Connect to: SharePoint, Dynamics 365, Teams, Outlook, Salesforce, ServiceNow, databases, and many more. Custom connectors for REST APIs are also supported.

### Multi-Agent Orchestration

| Pattern | Description |
|---------|------------|
| **Child agents** | Lightweight sub-agents within a parent; share tools/knowledge; no separate publishing |
| **Connected agents** | Independent, separately published agents the parent delegates to; reusable across parents |
| **Agent-to-Agent (A2A)** | Open protocol for cross-agent delegation (rolling out 2026) |

---

## 9. Use Cases by Business Department

### Finance & Accounting
- Invoice processing and expense management automation
- Spending analysis across spreadsheets with pattern identification
- Automated monthly/quarterly/year-end reporting
- Financial forecasting from historical data
- Compliance checking and regulatory validation

### Human Resources
- Employee onboarding assistant (25% faster onboarding reported)
- Virtual HR assistant for benefits, leave policies, procedures
- Resume screening and candidate comparison
- Offer letter and welcome email generation

### Marketing & Sales
- Content creation pipelines in brand voice
- Campaign management and brainstorming
- Marketing email acceleration (up to 75% time reduction)
- Presentation generation from data

### IT / Helpdesk
- Password resets, software installations, common IT requests
- Knowledge retrieval from manuals, logs, documentation
- Ticket auto-acknowledge, classify, and route
- Approval flows, alert systems, change management

### Operations
- Process improvement via Six Sigma reporting
- Supply chain optimization and vendor communication
- Inventory management
- Project planning and progress tracking

### Customer Service
- 24/7 customer-facing chatbots for FAQs, orders, returns
- Escalation to human agents with full context
- Multi-language support

---

## 10. Limitations to Watch Out For

### Knowledge Source Limits
- SharePoint files must be under **7 MB** (without M365 Copilot license)
- Only **modern SharePoint pages** supported (not classic ASPX)
- Queries referencing specific file names cannot be answered
- Complex documents (nested tables, diagrams) may be misinterpreted
- Video, audio, and image-based PDFs are largely inaccessible
- No real-time indexing — lag between document updates and agent awareness
- Password-protected or sensitivity-labeled docs cannot be indexed

### Rate Limits
- Generative AI requests are rate-limited per minute and per hour
- Exceeding limits blocks messages with throttling errors
- Admins can purchase additional capacity

### AI Behavior
- Token limits restrict how much knowledge is processed at once
- RAG may struggle when relevant data is scattered across many sections
- Combining generative answers with rigid conversation paths can cause unpredictable behavior
- The AI may "break out" of prescribed flows to answer generally

### API Plugin Limits
- No support for nested objects, polymorphic references, circular references
- No API keys in custom headers/cookies
- Only OAuth Authcode/PKCE flows supported

### General
- Best suited for organizations already in the Microsoft 365 ecosystem
- Those needing sub-second latency or total UI control may find limitations

---

## 11. Tips for Hackathon Success

1. **Start simple** — pick ONE clear use case and nail it before expanding
2. **Use AI-assisted creation** — let Copilot Studio build your first agent from a description
3. **Don't overthink it** — your first agent can be live in under 10 minutes. Iterate!
4. **Instructions are everything** — clear, specific instructions are the single highest-impact thing you can do
5. **Let generative answers do the work** — connect knowledge sources and let AI respond. You don't need to script every topic.
6. **Test constantly** — test after every change. Use "Track between topics" to follow flow
7. **Add knowledge thoughtfully** — always provide meaningful names and detailed descriptions
8. **Use templates** when available for a proven starting point
9. **Don't over-engineer** — if a simple topic works, don't create child agents. Reserve multi-agent patterns for genuinely complex scenarios
10. **Show a working demo** — judges care about a live demo more than slides
11. **Focus on business impact** — explain the time/cost savings your agent creates
12. **Remember: no chatbots** — your agent must automate a multi-step business process, not just answer questions

---

## 12. Pricing Quick Reference

| Option | Best For | Cost |
|--------|----------|------|
| **Capacity Pack** | Predictable, high-volume | $200/month per 25,000 Copilot Credits |
| **Pay-As-You-Go** | Variable or pilot usage | Per Credit consumed (Azure billing) |
| **Pre-Purchase** | Annual commitment | Up to 20% discount |
| **M365 Copilot** | Internal employee agents | Included in license |
| **Trial** | Testing/development | Free (no publishing) |

Credits consumed depend on agent design, interaction frequency, and features used.

---

## 13. Security and Governance

| Feature | Mechanism |
|---------|-----------|
| DLP Policies | Power Platform Admin Center |
| Authentication | Microsoft Entra ID (default on) |
| Connector Governance | 1,400+ connectors with DLP blocking |
| Data Encryption | In-transit and at-rest |
| Auditing | Activity Logs, Purview, Application Insights |
| Role-Based Access | Entra AI Admin roles |
| Compliance | SDL, Microsoft Trust Center |

---

## 14. Key Resources

| Resource | Link |
|----------|------|
| Copilot Studio | copilotstudio.microsoft.com |
| Agent Academy (free courses) | microsoft.github.io/agent-academy |
| Quickstart Guide | learn.microsoft.com/microsoft-copilot-studio/fundamentals-get-started |
| Training Path | learn.microsoft.com/training/paths/create-extend-custom-copilots-microsoft-copilot-studio |
| Multi-Agent Labs | microsoft.github.io/mcs-labs/labs/mcs-multi-agent |
| Adoption Hub | adoption.microsoft.com/en-us/ai-agents/copilot-studio |

---

## 15. Mental Model (Buildathon Slide Deck)

> **Source:** _Copilot Studio Mental Model_ by Vishal Sachdev — the live slide deck at
> [gies-ai-experiments.github.io/gies-buildathon/docs/slides-mental-model](https://gies-ai-experiments.github.io/gies-buildathon/docs/slides-mental-model/dist/index.html).
> Summarised here so the agent can answer "what is Copilot Studio?", "what can I build?", and "where do I start?" directly from memory.

### What is Copilot Studio?

- Microsoft's **low-code platform** to build AI agents and automations
- Describe what you want in **plain language** — the AI helps build it
- Already connected to your **Microsoft 365** data (Outlook, Teams, SharePoint, Excel, Planner, Forms, OneDrive, Word)
- Access at **copilotstudio.microsoft.com**

On the home screen it asks: **"What would you like to build?"** — Agent or Workflow.

### Two Things You Can Build

| | **Agent** | **Workflow (Agent Flow)** |
|---|---|---|
| What it is | A chat interface that **knows things** and **does things** | An automation that **runs on a trigger** |
| Starts from | A user asking a question | Schedule, event, or manual start |
| Determinism | Uses AI to understand intent | Deterministic — same input, same output |
| Multi-step | Primarily single-turn per question | Multi-step, chain actions together |
| Typical use | *"What's our PTO policy?"* | *"Every Monday 8am, summarize my emails"* |

**Key insight:** An agent can trigger a workflow **mid-conversation**. That's the bridge — the agent handles the natural-language part, the workflow handles the deterministic multi-step automation.

### Inside an Agent — Building Blocks

```
YOUR AGENT
├── Instructions   — personality, rules, guardrails (up to 8,000 chars)
├── Knowledge      — websites, SharePoint, uploaded files (RAG)
├── Topics         — structured conversation paths with branching
├── Tools          — single-step connectors (send email, read Excel)
└── Agent Flows    — multi-step automations the agent can trigger
```

**You don't need all of these.** Knowledge alone gets you a working chatbot in 10 minutes.

### What Can Connect?

**Works out of the box** — Microsoft-native, real-time API calls, no admin approval:

- Outlook · Teams · Planner · Excel · SharePoint · OneDrive · Word · Forms

**Needs admin approval** — external connectors, enterprise security auth required:

- Asana · ServiceNow · Salesforce · Custom APIs

**For the hackathon:** stick to Microsoft-native connectors. They just work.

### From Chatbot to Agent — Progression Levels

| Level | What to add | Example | Time to build |
|---|---|---|---|
| **1. Chatbot** | Knowledge only | *"Ask me about Gies programs"* | 10 min |
| **2. Structured** | + Topics with branching | *"Which program?"* → MBA / MSBA / Undergrad | 30 min |
| **3. Connected** | + Tools (connectors) | *"I'll create a Planner task for your appointment"* | 1–2 hr |
| **4. Automated** | + Agent Flows | *"Email advisor + log in Excel + post to Teams"* | 2–4 hr |
| **5. Multi-agent** | + Sub-agents | *"Let me hand you to our financial aid agent"* | 4+ hr |

**Hackathon target:** aim for Level 3–4. Level 5 is the moonshot.

### Where to Get Help

- **Copilot inside Studio** — click the Copilot button in Studio and ask in plain English how to do things.
- **MS Learn Docs** — [learn.microsoft.com/copilot-studio](https://learn.microsoft.com/copilot-studio) — has its own built-in chat assistant.
- **ChatGPT or Claude** — *"How do I connect Excel to my Copilot Studio agent?"* actually works; these tools know the platform.
- **Mentors & teammates** — Brian & James (CIO AI team) and your teammates. Pair up instead of solo-debugging.
