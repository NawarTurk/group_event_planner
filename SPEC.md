# SPEC.md

## Working Title
**PlanTogether**

Name is temporary and can be changed later.

## One-Sentence Product
A Discord-native group planning assistant that combines each member's preferences, shared calendar availability, current events, weather, and review context to propose plans the group can refine and vote on.

## Why This Fits the Hackathon
The hackathon asks teams to build agents that show up inside places people already work, talk, and live instead of forcing users into another standalone chat experience.

This project lives directly inside **Discord**, where the group already communicates.

The agent is not the product by itself. The product is the group decision experience inside Discord. The **OpenAI Agents SDK** powers the reasoning and tool use behind that experience, while **Exa** provides current event and web discovery.

## Sponsor Technologies Used
For the current build, deliberately use:

### OpenAI
- OpenAI Agents SDK for the agent loop and tool orchestration
- OpenAI model for reasoning and response generation

### Exa
- search for real current events and activities
- web discovery and useful context around candidate options
- review/reputation context where appropriate

Do not force additional sponsor integrations into the MVP unless they clearly improve the product.

## Current Status
Already working:
- Discord application created
- Discord bot created and added to the server
- bot runs locally using `discord.py`
- bot receives messages and responds successfully
- Discord token is loaded from `.env`

The next milestone is **Discord -> OpenAI Agents SDK -> Discord**.

## Target Users
Small groups of friends who already use Discord and waste time figuring out:
- what to do
- when everyone is free
- whether an event fits everyone's preferences
- whether weather makes an outdoor plan a bad idea
- whether an event/place is actually worth going to

## Primary Demo Users
Two users for the hackathon demo:
- Nawar
- Akash

The design should not depend on exactly two users, but two users is enough for the MVP.

## Discord Server Layout
Use a server with at least these channels:

```text
#planning
#nawar-preferences
#akash-preferences
```

### Preference Channels
Each user writes persistent preferences in their personal preference channel.

Example `#nawar-preferences`:

```text
I like chicken and Middle Eastern food.
Budget is usually under $50.
I like live music and interesting events.
I prefer outdoor activities when the weather is comfortable.
```

Example `#akash-preferences`:

```text
I am vegetarian.
I prefer places with good vegetarian options.
I like art, music, and casual activities.
I don't like very expensive places.
```

For the MVP, the bot may read recent channel history each time a plan is requested.

## Main User Flow

### 1. Request
In `#planning`, a user writes something like:

```text
@PlanTogether suggest something for me and Akash this weekend
```

or:

```text
@PlanTogether what should we do Saturday evening?
```

### 2. Gather Group Preferences
The agent identifies the requested participants and reads their preference channels.

It should extract relevant constraints such as:
- dietary restrictions
- food likes/dislikes
- budget
- activity preferences
- indoor/outdoor preference
- vibe
- recurring dislikes

### 3. Check Shared Calendar Availability
The agent uses Google Calendar OAuth credentials for each participating user.

It determines overlapping free time in the requested window.

Example result:

```text
Both users are free Saturday from 5:30 PM to 9:30 PM.
```

If calendar event locations are present, the agent may use them as a clue that a user is already near a neighborhood or area.

It must never claim to know the user's live location.

### 4. Check Weather
The agent checks weather for the relevant time/location.

Weather should affect ranking, not just appear as decoration.

Examples:
- heavy rain -> strongly favor indoor events
- very cold -> favor indoor options or warn clearly
- pleasant mild weather -> outdoor activities get a boost
- strong sun/heat -> avoid exposed outdoor events or include a warning

### 5. Search Current Events / Activities with Exa
Use **Exa** to search for real, timely options matching:
- shared availability
- location
- budget
- group preferences
- weather

Prefer event-oriented suggestions over generic recommendations when possible.

Examples:
- festival
- live music
- exhibition
- comedy
- food market
- trivia
- pop-up event
- sports/recreation
- unusual local activity

### 6. Check Review / Reputation Context
For promising candidates, use Exa/web context to find useful reputation signals. Reddit can be included when it provides useful discussion.

Useful signals might include:
- generally liked or disliked
- overpriced
- too crowded
- good vegetarian selection
- long lines
- good atmosphere

Do not overbuild this. One or two useful signals per candidate is enough.

Do not present unsupported claims as facts.

### 7. Generate 2–3 Options
The agent returns only the strongest 2–3 plans.

Each option should include:
- activity/event name
- proposed time
- location
- approximate price if available
- indoor/outdoor
- a short explanation of why it matches the group
- any useful weather warning
- one short review/reputation insight when available

Example output:

```text
1. Indie music night — Saturday 7:00 PM
   $25–35 | Indoors | Downtown
   Fits both calendars. Vegetarian food nearby, within budget, and web feedback is generally positive about the atmosphere.

2. Outdoor food festival — Saturday 5:30 PM
   ~$30 | Outdoors | Old Montreal
   Good food variety for both of you. Weather should be mild and dry, so this gets an outdoor boost.

3. Evening exhibition + café — Saturday 6:00 PM
   ~$20–30 | Indoors
   Strong match for Akash's art preference and Nawar's preference for something different from a normal dinner.
```

## Feedback / Refinement Flow
Before voting, users can reply naturally.

Examples:

```text
I like option 1 but it is too expensive.
```

```text
Keep option 2's time, but make it indoors.
```

```text
Something closer to downtown.
```

```text
I don't want food to be the main activity.
```

The agent should preserve accepted constraints and revise only what needs changing.

Example:

```text
Got it. Keeping Saturday around 6 PM, switching to indoor options, and targeting under $30/person.
```

Then return a revised set of 2–3 options.

## Voting
Once users are satisfied, allow a simple vote.

MVP implementation can use:
- Discord reactions, or
- Discord buttons

Example:

```text
Vote:
1️⃣ Indie music night
2️⃣ Exhibition + café
3️⃣ Comedy show
```

After voting, announce the winning option.

For the hackathon MVP, **stop here**.

Do not require automatic booking or calendar creation.

## Agent Behavior
Use **one OpenAI agent** for the MVP.

The OpenAI Agents SDK agent should decide which tools to call based on the request.

Likely conceptual sequence:

```text
Discord request
  -> identify participants
  -> read preferences
  -> check common calendar availability
  -> check weather
  -> search events with Exa
  -> inspect useful web/review context
  -> rank options
  -> post suggestions
  -> receive feedback
  -> revise
  -> vote
```

This sequence is conceptual. Do not make the implementation unnecessarily rigid. Let the agent invoke explicit tools as needed.

## Core Tools

### `get_user_preferences`
Input:
- Discord user ID

Output:
- normalized preferences/constraints

### `get_common_availability`
Input:
- participating Discord user IDs
- requested date/time range

Output:
- overlapping available time windows

### `get_weather`
Input:
- location
- datetime/time window

Output:
- temperature
- rain probability/conditions
- simple activity suitability signal

### `search_events_with_exa`
Input:
- location
- time window
- preference constraints
- budget
- indoor/outdoor preference

Output:
- candidate real-world events/activities with source context

### `search_review_context`
Input:
- event/place name

Output:
- concise useful reputation/review signals found through web search

### `get_recent_group_history`
Optional for MVP if time allows.

Purpose:
- avoid recommending the same place/activity repeatedly

### `create_vote`
Input:
- final options

Output:
- Discord vote UI/message

## Google Calendar Authentication
Each Discord user authorizes their own Google account using OAuth 2.0.

The application must maintain a mapping such as:

```text
Discord user ID -> Google OAuth token set
```

Codex can implement the OAuth flow, token storage interface, and Calendar API integration.

The humans running the project must still create the Google Cloud OAuth client credentials and configure the consent screen/client settings.

Do not commit user OAuth tokens.

## Discord Authentication
The bot is a normal Discord bot account created in the Discord Developer Portal.

This part is already working.

The application uses a bot token stored in `.env`:

```text
DISCORD_TOKEN=...
```

Users do not need personal Discord API tokens.

## OpenAI Authentication
The running agent uses an OpenAI API key stored in `.env`:

```text
OPENAI_API_KEY=...
```

Never hard-code or commit this key.

## Exa Authentication
When Exa integration is added, use an Exa API key stored in `.env`:

```text
EXA_API_KEY=...
```

Never hard-code or commit this key.

## Runtime Architecture
Use one service for the MVP:

```text
Discord
   |
   v
Existing Python discord.py bot
   |
   v
OpenAI Agents SDK agent
   |
   +--> preference-channel reader
   +--> Google Calendar
   +--> weather
   +--> Exa event/web search
   +--> review/context search
```

No FastAPI or Flask is required for the initial implementation.

The service can run locally during development/demo preparation. If remote hosting is desired, containerize it and deploy to a host suitable for a long-running Python process.

## Non-Goals for the 4-Hour Build
Do not spend time on:
- native mobile apps
- standalone web UI
- live GPS tracking
- restaurant booking
- ticket purchasing
- payment handling
- complex user accounts
- sophisticated recommendation ML
- vector databases unless genuinely needed
- elaborate long-term memory
- multi-agent architecture
- production-grade scaling

## Success Criteria
The MVP is successful if, during the demo:

1. The Discord bot is visibly present in the server.
2. Two users have different preferences in their personal channels.
3. A user asks for a plan in the shared channel.
4. The OpenAI agent uses both users' preferences.
5. The agent finds a shared available time from both calendars.
6. The agent uses **Exa** to find real current event/search results.
7. Weather changes or influences the recommendation.
8. At least one useful review/reputation signal is incorporated.
9. The bot returns 2–3 understandable options.
10. A user gives natural-language feedback and the options change appropriately.
11. The users vote.
12. The bot announces the winner.

## Build Plan

### Milestone 0 — DONE
Discord bot works.

```text
Discord message -> discord.py -> hard-coded response
```

### Milestone 1 — NEXT
Connect the working Discord bot to a minimal OpenAI Agents SDK agent.

```text
Discord message
   -> discord.py
   -> OpenAI Agents SDK
   -> OpenAI model
   -> final response
   -> Discord
```

Milestone 1 must **not** include:
- preference channels
- Google Calendar
- Exa
- weather
- reviews
- voting
- persistence
- deployment changes

### Milestone 2
Read Nawar and Akash preference channels and provide them as agent context/tools.

### Milestone 3
Connect both users' Google Calendars and find common free time.

### Milestone 4
Add **Exa** for current event discovery and web context.

### Milestone 5
Add weather and use it to influence indoor/outdoor ranking.

### Milestone 6
Add useful review/reputation context.

### Milestone 7
Return 2–3 ranked plans, accept natural-language feedback, revise, and vote.

## Hackathon Submission Checklist
Before submission, make sure the project has:
- a clear title
- a written description explaining what was built, who it is for, and why Discord context matters
- a public GitHub repository with working code
- a concise 2-minute demo video
- a public social post that tags the event sponsors as required

The demo should emphasize the working Discord experience instead of explaining architecture for most of the video.

## Suggested 2-Minute Demo Script
**0:00–0:20**
Show the Discord server and the two preference channels.

**0:20–0:35**
Show that both users have different preferences and calendars connected.

**0:35–0:50**
In `#planning`, type:

```text
@PlanTogether suggest something for us Saturday evening
```

**0:50–1:15**
Show the bot returning 2–3 real options and briefly point out:
- calendar overlap
- preferences
- weather
- Exa-powered current event discovery
- useful review/reputation context

**1:15–1:35**
Reply:

```text
I like option 1, but make it cheaper and indoors.
```

Show the agent adapt.

**1:35–1:55**
Vote on the revised options.

**1:55–2:00**
Show the winner and end.

## Immediate Codex Task
Codex should now implement only Milestone 1.

Use this instruction:

```text
Read AGENTS.md and SPEC.md first.
The Discord bot already works and responds to messages. Preserve the existing discord.py integration.
Implement only Milestone 1: add the OpenAI Agents SDK for Python, create one minimal OpenAI agent, route ordinary Discord messages to the agent, and send the agent's final response back to the same Discord channel.
Use OPENAI_API_KEY from .env.
Do not add preference channels, Google Calendar, Exa, weather, reviews, voting, persistence, a web server, or deployment changes yet.
Keep the implementation minimal and explain the commands required to install dependencies and run it.
```
