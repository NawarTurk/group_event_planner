# AGENTS.md

## Project Goal
Build a working hackathon prototype of a **Discord-native group planning agent** using **Python + OpenAI Agents SDK**.

The product is the Discord experience. The agent is the engine behind it.

The agent should help two or more people choose an activity/event by reading their stated preferences, checking shared availability, searching current options, using weather and review context, and proposing 2–3 plans inside Discord.

Read `SPEC.md` before making implementation decisions.

## Current Project Status
The Discord integration is already working.

Completed:
- Discord application and bot created.
- Bot added to the shared Discord server.
- `discord.py` bot connects successfully.
- Bot can read and respond to messages.
- Secrets are loaded from `.env`.

**Do not rebuild or replace the working Discord integration unless necessary.** Preserve the existing working bot and extend it incrementally.

## Hackathon Constraints
- The agent must live in a place people already use. For this project, that place is **Discord**.
- Do not build a separate chatbot UI unless strictly necessary.
- Prioritize a **sharp, working demo** over a broad feature set.
- The demo must be understandable in about 2 minutes.
- Keep the code public-repo friendly and easy for judges to run.
- Sponsor tools should be used only when they genuinely improve the product.

## Sponsor Stack
For now, the project intentionally uses these hackathon sponsor technologies:

### OpenAI
Use OpenAI for:
- the **OpenAI Agents SDK** as the agent framework
- the LLM/reasoning layer
- tool selection and orchestration

### Exa
Use Exa for:
- discovering current events and activities
- finding relevant web context
- finding useful review/reputation context when appropriate

Do not add additional sponsor integrations unless there is a clear product reason or the core MVP is already working.

## MVP Scope
Build only this flow:

1. Users maintain preferences in dedicated Discord channels.
2. A user asks in the shared planning channel for an event/activity suggestion.
3. The agent reads the relevant preference channels.
4. The agent checks both users' Google Calendars for overlapping free time.
5. The agent checks weather for the candidate time/location.
6. The agent uses **Exa** to search for real events/activities and useful web context.
7. The agent may use Reddit/review context when useful.
8. The agent proposes 2–3 concrete options in Discord.
9. Users can reply with feedback such as:
   - "I like option 1 but it is too expensive."
   - "Keep the time but make it indoors."
   - "Something closer to downtown."
10. The agent quickly revises the options.
11. Users vote on the options.
12. For the MVP, the workflow **stops after the vote**.

Do not add booking, payments, calendar event creation, navigation, or live-location tracking unless the core MVP is already working.

## Required Tech Direction
- Language: **Python**
- Agent framework: **OpenAI Agents SDK for Python**
- Model provider: **OpenAI**
- Discord integration: **discord.py**
- Calendar: **Google Calendar API with OAuth 2.0 per user**
- Search: **Exa**
- Weather: a simple weather API/tool
- Reddit/reviews: use a simple search-based approach first; do not spend hackathon time building complex scraping
- Persistence: start with lightweight local persistence such as SQLite or JSON if needed

## Runtime Architecture
For the MVP, use **one long-running Python service**:

```text
Discord
  <-> discord.py bot
  <-> OpenAI Agents SDK agent
  <-> tools/APIs
      +--> preference reader
      +--> Google Calendar
      +--> Exa
      +--> weather
      +--> reviews/context
```

Do **not** introduce FastAPI or Flask unless there is a concrete need.

The Discord bot process and OpenAI agent should run in the same Python application for the hackathon MVP.

During development, running locally is acceptable. For remote deployment, use a host suitable for a long-running Python process.

## Secrets and Credentials
Never hard-code credentials.

Use `.env` locally and provide `.env.example` with names only.

Expected environment variables may include:

```text
DISCORD_TOKEN=
OPENAI_API_KEY=
EXA_API_KEY=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=
WEATHER_API_KEY=
```

Never commit:
- Discord bot token
- OpenAI API key
- Exa API key
- Google OAuth client secret
- Google user refresh/access tokens
- weather API keys

Google Calendar authorization is per Discord user. Store the mapping between Discord user ID and that user's Google OAuth credentials/tokens securely.

## Discord Structure
Assume configurable channel IDs, for example:

```text
#planning
#nawar-preferences
#akash-preferences
```

Do not hard-code channel names throughout the code. Put IDs/names in configuration.

Preference channels contain durable statements such as:
- vegetarian
- likes chicken
- budget range
- prefers live music
- dislikes crowded places
- prefers indoor activities in bad weather
- likes outdoor activities when weather is comfortable

For the MVP, reading recent channel history is acceptable. A structured preference store can be added later.

## Agent Design
Use one primary OpenAI agent for the MVP. Do not create a multi-agent architecture unless there is a clear need later.

The agent should have small, explicit tools. Prefer tool calls over one giant function.

Suggested tools:

```text
get_user_preferences(discord_user_id)
get_group_preferences(user_ids)
get_common_calendar_availability(user_ids, date_range)
get_weather(location, datetime)
search_events_with_exa(query, location, datetime, constraints)
search_review_context(query)
get_recent_group_history(user_ids)
create_vote(options)
```

The agent should reason over tool outputs and decide which tools are needed. Avoid implementing the whole workflow as one rigid hard-coded pipeline.

## Recommendation Rules
Recommendations should consider:
- all users' explicit preferences
- dietary constraints
- budget
- common free time
- event timing
- weather
- indoor vs outdoor suitability
- event location
- whether users are already near an area based on calendar event locations, if available
- recent group history to avoid repetitive suggestions when possible
- review/reddit sentiment when useful

Do not claim live location. Calendar event locations are only contextual hints.

Each recommendation should briefly explain **why it fits the group**.

Example:

```text
Option 1: Outdoor food festival, 5:30–7:30 PM
Why it fits: both calendars are free, vegetarian + chicken options are available,
weather is mild and dry, and you have not done this recently.
```

## Feedback Loop
Treat user feedback as constraints on the current planning session.

Examples:
- "Too expensive" -> reduce budget
- "Keep option 2's time" -> preserve time constraint
- "No outdoor" -> indoor only
- "Something more active" -> revise activity type

Do not restart from scratch unless necessary. Preserve accepted parts of prior options.

## Voting
Keep voting simple.

Preferred MVP options:
- Discord reactions, or
- buttons/components if quick to implement

The bot should summarize the final winning option after the vote.

No post-vote action is required for the MVP.

## Coding Rules
- Preserve the existing working `discord.py` bot.
- Keep modules small and readable.
- Use type hints for public functions.
- Add concise docstrings where behavior is non-obvious.
- Prefer async code where Discord/API calls require it.
- Handle API/network failures gracefully.
- Log important actions without logging secrets.
- Add minimal tests for pure logic such as preference merging and recommendation filtering.
- Keep vendor-specific code behind small adapters where practical.
- Avoid premature abstractions.
- Keep every milestone runnable before moving to the next one.

## Suggested Project Structure

```text
project/
  AGENTS.md
  SPEC.md
  README.md
  .env
  .env.example
  .gitignore
  requirements.txt or pyproject.toml
  bot.py
  app/
    config.py
    agent.py
    tools/
      preferences.py
      calendar.py
      weather.py
      exa_search.py
      reviews.py
      history.py
      voting.py
    storage/
      db.py
  tests/
```

This is a guideline, not a strict requirement. Do not reorganize working code just for aesthetics during the hackathon.

## Build Order
Follow this order unless there is a strong reason not to:

1. **DONE:** Discord bot joins the server and replies to messages.
2. **NEXT:** Add OpenAI Agents SDK and make Discord messages reach a minimal OpenAI agent, then send the agent response back to Discord.
3. Read preference channels.
4. Add Google Calendar OAuth for two users and find common free time.
5. Add **Exa** event/web search.
6. Add weather.
7. Add review/Reddit context if useful.
8. Generate 2–3 ranked options.
9. Add feedback-based revision.
10. Add voting.
11. Clean README and demo flow.

At every stage, keep the previous stage runnable.

## Immediate Next Milestone
Implement only the smallest OpenAI vertical slice:

```text
Discord message
    -> existing discord.py handler
    -> OpenAI Agents SDK agent
    -> agent final response
    -> same Discord channel
```

Requirements for this milestone:
- preserve the working Discord bot
- use `OPENAI_API_KEY` from `.env`
- create one minimal agent
- send ordinary Discord messages to that agent
- send the returned final output back to Discord
- do not add preferences, calendars, Exa, weather, reviews, voting, persistence, or deployment yet

Once this works reliably, move to preference-channel reading.

## Demo Priority
The ideal final demo is:

1. Show two preference channels.
2. In `#planning`, type: "Suggest something for us this weekend."
3. Bot uses both users' context and calendars.
4. Bot uses Exa to find real current options.
5. Weather influences the recommendations.
6. Bot proposes 2–3 plans.
7. A user gives natural-language feedback.
8. Agent adapts the options.
9. Users vote.
10. Bot announces the winner.
