# PlanTogether

One Python service connecting the existing Discord bot to one OpenAI Agents SDK
agent. The agent selects Exa event search, preference ranking, and Open-Meteo tools. The bot
currently responds in **#general** and reads the existing `nawar-preferences`
and `akash-preferences` channels.

## Run locally

Use Python 3.10 or newer. From this project directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python bot.py
```

Before starting, ensure your existing `.env` contains `DISCORD_TOKEN` and
`OPENAI_API_KEY`. For a fresh checkout, create `.env` using the variable names in
`.env.example`. Event search uses `EXA_API_KEY`; no weather key is required.
Keep the bot's existing Discord message-content intent and channel permissions.
Stop an already-running bot before starting the updated process.

## Application audit log

The existing `#agent_log` receives a startup message with a six-character instance
ID, once per process and guild. Startup no longer posts to `#general`.
Each executed weather lookup posts one completion message with tool name,
location, requested date/time, actual status, and Open-Meteo on success.
These messages come from Python after tool execution, never from model answers.
`audit.py` provides reusable best-effort sending and tool-completion formatting.
Missing channels or write permissions produce terminal warnings without blocking
the normal answer in `#general`. Startup delivery is attempted only once per guild,
including when that attempt fails, to avoid reconnect duplicates.
Audit messages exclude prompts, preference contents, full results and exception
details; configured secrets are redacted and Discord mentions are disabled.

Every nonempty user request in `#general`, including greetings, independently
checks both preference channels before the agent runs. One `📚` audit message
reports each channel's loaded message count, `empty`, `missing`, or `access failed`.
Counts include all current non-bot messages with text in each channel's history.
An incomplete read is discarded; the other channel is still checked. The audit says
`Preferences loaded` only after retrieved context is added to the prompt, or
`Preferences checked` when no context was available. Preference contents and author
names never appear in this audit. Logging failures do not block the response.

## Dynamic event discovery and comparison

Optional `.env` settings (blank values use these defaults):

| Variable | Default |
| --- | --- |
| `LOCAL_TIMEZONE` | `America/Toronto` |
| `DEFAULT_EVENT_LOCATION` | `Montreal, Quebec, Canada` |
| `EVENT_SEARCH_DAYS` | `7` |

`events.py` reads the clock in the configured timezone on each request. Today and
tomorrow resolve locally; this weekend means the remaining Saturday/Sunday dates;
next week means the following Monday–Sunday. An omitted timeframe searches today
through `EVENT_SEARCH_DAYS - 1` days ahead. A stated city overrides the default.
No calendar dates or user preference values are stored in production code.

Search uses the official `exa-py==2.14.0` async SDK, `type="auto"`, and the
[Exa search guide](https://docs.exa.ai/reference/search-api-guide-for-coding-agents)
syntax. It requests 15 candidates by default, using only location and calculated
timeframe—never preferences. If fewer than three verified candidates survive,
it retries once with a simpler city/timeframe query, then deduplicates both sets.
Each request has a 30-second timeout. Exa is never called without its key.

Verification requires a source URL, page-backed title, explicit full event date,
and matching city. Publication date is not event date. Past dates and known
already-started events today are excluded. Unknown same-day times are labeled
unconfirmed. Fields without source support become null; food, price, venue, vibe,
and setting are optional. Current date parsing accepts ISO and full English dates;
unsupported source formats are left out rather than guessed. “Verified” refers
to source-backed details, not independently confirmed ticket availability.

After broad search, the same primary agent extracts every stated criterion from
the current Discord messages and assesses every candidate separately for each
person. The local `rank_events` tool checks source references, fills omitted or
unsupported assessments as `❓ Not confirmed`, and performs the ranking in Python:

- Each `✅ Confirmed match` adds one point.
- Each `❌ Confirmed conflict` subtracts one point.
- Unknowns add zero; optional missing fields never remove an event.
- The weakest user's net score is added as a balance bonus, then the top three
  are selected. No perfect intersection is required.

Each criterion is rendered separately, including conflicts. The model interprets
the natural-language preferences and comparisons; tests simulate those judgments
while verifying source gates, coverage, scores, and rendering. A source message
must be covered, and all its distinct criteria are requested in the instructions.
Edits are fetched on the very next request, with no preference cache.

Exa completion logs initially show the real candidate count. After Python ranks
the results, that same audit message is updated to show `N candidates, M ranked`.
No ranking count is claimed before ranking executes. Existing startup, preference,
and weather logs remain application-generated.

Scorecards include each person's available criteria, verified source links, and
relevant retrieved weather. They are sent in one Discord reply. Longer comparisons
use Discord embeds, or a text attachment when necessary, so criteria and URLs
are not silently truncated. Events in the matching city are local; no distance or
“nearby” claims are made without distance evidence.

To try the complete flow in `#general`, send `Find events for us next week`.
The configured city is used, preferences are fetched, events are searched broadly,
and the top three receive independent criterion checks for each person.

## Test in Discord

In **#general**, send each message separately:

- `What is the weather in Montreal now?` should prompt the agent to call the weather tool,
  resolves Montreal to Quebec, Canada, and uses the current local forecast hour.
  The terminal prints `WEATHER TOOL CALLED: Montreal` (or its expanded location).
  The SDK returns the tool result to the model for one final Discord answer.
  Lookup failures log the exception and send one failure message.
- `waht is the wether in montrel now` should also trigger a lookup through the
  model's understanding of the request, without keyword matching.
- `Help us plan a picnic in Montreal tomorrow.` should prompt a weather lookup
  and use it to recommend an outdoor plan or indoor alternative.
- `Suggest an indoor board game.` should receive an answer without a lookup.

Direct weather answers should be at most two short sentences, with Celsius
temperature, main conditions, relevant precipitation chance, at most one practical
tip, and Open-Meteo attribution. More detail is provided only when requested.

1. `Suggest 2 activities for us in Toronto, Ontario, Canada tomorrow at 18:00. Check the weather and explain indoor versus outdoor choices.`
   Expect a local hourly forecast with Celsius temperature, precipitation chance,
   conditions, Open-Meteo attribution, and suggestions influenced by the weather.
2. `Check weather for Toronto, Ontario, Canada tomorrow and suggest a plan.`
   Expect a labeled whole-day temperature range and maximum hourly precipitation
   probability, without an invented activity time.
3. `Suggest an outdoor plan tomorrow based on the weather.`
   Expect a question asking for location.
4. `Suggest a weather-appropriate plan in Toronto, Ontario, Canada.`
   Expect a question asking for the date.
5. `Check weather in Springfield tomorrow.`
   Expect location clarification if the geocoder returns multiple matches.
6. `Check weather in Toronto, Ontario, Canada on 2099-01-01.`
   Expect an unavailable-forecast explanation, not invented weather.

The existing bot does not retain conversation history. When answering a
clarification, repeat the full request with city, date, and optional time.
Live weather varies: rain/cold/snow should favor indoor ideas; pleasant weather
should permit outdoor ideas. Tests below verify these cases deterministically.

## Automated checks

```sh
DISCORD_TOKEN=test-token OPENAI_API_KEY=test-key python -m pytest -q
```

Tests mock all provider calls and do not connect to Exa, weather, Discord or OpenAI.
They cover hourly/daily selection, indoor/outdoor guidance, missing/ambiguous
inputs, invalid dates, forecast limits, missing data, timeouts, and SDK schema.

## Weather implementation

`weather.py` uses asynchronous `aiohttp` requests with a 15-second timeout per
request. `aiohttp` is now an explicit dependency; discord.py already depends on it.
`bot.py` registers `get_weather` using the SDK's `function_tool` and gives the
agent weather-planning instructions. Existing routing and preference reading
are preserved.

Forecasts use [Open-Meteo](https://open-meteo.com/en/docs), with city resolution
through its [geocoding API](https://open-meteo.com/en/docs/geocoding-api).
Every request uses `ModelSettings(tool_choice="auto", parallel_tool_calls=False)`.
The model decides whether weather matters, and the SDK runs its normal tool loop.
There is no keyword routing or manual change to tool choice. The wrapper executes
each requested lookup with its own arguments and sends only the final Discord answer.
Mock-model tests verify tool configuration, execution, result feedback, and the
no-tool path; they do not prove a live model's semantic choices or response length.

Dates must be within today through 15 days ahead in the resolved location's
timezone. `today`, `tomorrow`, and `now` are resolved there, not in the server timezone.
Minutes select the containing forecast hour. Date-only requests summarize the
whole day, including overnight; hourly requests are better for activity planning.
Precipitation probability includes snow, not just rain.

Comfort rules are simple heuristics: below 10°C, at least 30°C, precipitation
chance at least 40%, or fog/rain/snow/storm codes favor indoors. Below 15°C adds a
jacket warning. These are planning hints, not official weather alerts.
On API failure or incomplete data, the tool reports weather unavailable.
# group_event_planner
