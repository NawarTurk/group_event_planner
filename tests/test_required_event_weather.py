import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents.tool_context import ToolContext

import bot
from event_weather import forecast_for_event, weather_key
from ranking import Assessment, Criterion, rank_candidates, render_scorecards


def event(index, day="2033-04-10", city="Montreal", time="7:00 PM"):
    return {"title": f"Event {index}", "date": day, "location": city, "time": time,
            "venue": "Venue", "url": f"https://venue.example/{index}", "activities": "Music"}


def forecast(status="ok"):
    return {"status": status, "conditions": ["Rain"], "temperature_c": {"min": 12, "max": 12},
            "precipitation_probability_percent": 80, "warnings": ["Thunderstorms expected; prefer indoors."]}


def setup(monkeypatch, rows, weather_result=None, preferences=None):
    sent_log = SimpleNamespace(edit=AsyncMock())
    log = SimpleNamespace(name="agent_log", send=AsyncMock(return_value=sent_log))
    general = SimpleNamespace(name="general", send=AsyncMock())
    channels = [general, log]
    preferences = preferences or {}
    for person, content in preferences.items():
        async def history(limit, text=content):
            yield SimpleNamespace(content=text, author=SimpleNamespace(bot=False, display_name="User"))
        channels.append(SimpleNamespace(name=f"{person.lower()}-preferences", history=history))
    message = SimpleNamespace(content="Find events", channel=general, guild=SimpleNamespace(text_channels=channels))
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(bot, "search_events", AsyncMock(return_value={"status": "ok" if rows else "no_results",
                        "events": rows, "location": "Montreal", "date_or_timeframe": "calculated range"}))
    weather = AsyncMock(return_value=weather_result or forecast())
    monkeypatch.setattr(bot, "get_weather", weather)
    return message, log, weather


async def invoke(agent, name, args):
    text = json.dumps(args)
    tool = next(t for t in agent.tools if t.name == name)
    return await tool.on_invoke_tool(ToolContext(context=None, tool_name=name, tool_call_id=name, tool_arguments=text), text)


SEARCH = {"location": "Montreal", "date_or_timeframe": "next week", "max_results": 15}


@pytest.mark.parametrize("rows,count", [
    ([event(1), event(2), event(3)], 1),
    ([event(1), event(2, day="2033-04-11"), event(3)], 2),
    ([event(1), event(2, city="Toronto"), event(3)], 2),
])
def test_successful_search_gets_weather_for_each_unique_shortlisted_date_location(monkeypatch, rows, count):
    message, log, weather = setup(monkeypatch, rows)

    async def run(agent, prompt):
        await invoke(agent, "search_events", SEARCH)
        result = await invoke(agent, "rank_events", {"criteria": [], "assessments": []})
        assert result["status"] == "ok"
        assert weather.await_count == count
        message.channel.send.assert_not_called()
        return SimpleNamespace(final_output="Done")

    monkeypatch.setattr(bot.Runner, "run", run)
    asyncio.run(bot.ask_agent(message))
    assert weather.await_count == count
    assert all(call.args[2] == "19:00" for call in weather.call_args_list)
    assert len([call for call in log.send.call_args_list if call.args[0].startswith("🔧")]) == count
    message.channel.send.assert_awaited_once()
    call = message.channel.send.call_args
    text = call.args[0] if call.args else "\n".join(e.description for e in call.kwargs["embeds"])
    assert text.count("❓ Weather: No personal weather preference provided") == 6


def test_unknown_start_time_requests_whole_day(monkeypatch):
    message, _, weather = setup(monkeypatch, [event(1, time=None)])
    async def run(agent, prompt):
        await invoke(agent, "search_events", SEARCH)
        await invoke(agent, "rank_events", {"criteria": [], "assessments": []})
        return SimpleNamespace(final_output="Done")
    monkeypatch.setattr(bot.Runner, "run", run)
    asyncio.run(bot.ask_agent(message))
    weather.assert_awaited_once_with("Montreal, Quebec, Canada", "2033-04-10", None)


def test_premature_model_final_is_continued_after_real_weather_finishes(monkeypatch):
    message, _, weather = setup(monkeypatch, [event(1)])
    calls = []

    async def lookup(*args):
        message.channel.send.assert_not_called()
        await asyncio.sleep(0)
        calls.append("weather finished")
        return forecast()
    weather.side_effect = lookup

    async def run(agent, prompt):
        message.channel.send.assert_not_called()
        if not calls:
            await invoke(agent, "search_events", SEARCH)
            return SimpleNamespace(final_output="Premature recommendations must not be sent")
        assert calls == ["weather finished"]
        assert "Application completion check" in prompt[-1]["content"]
        await invoke(agent, "rank_events", {"criteria": [], "assessments": []})
        return SimpleNamespace(final_output="Done")

    runner = AsyncMock(side_effect=run)
    monkeypatch.setattr(bot.Runner, "run", runner)
    asyncio.run(bot.ask_agent(message))
    assert runner.await_count == 2
    weather.assert_awaited_once()
    message.channel.send.assert_awaited_once()
    assert "Premature" not in message.channel.send.call_args.args[0]


def test_zero_search_results_never_trigger_weather_even_if_model_requests_it(monkeypatch):
    message, log, weather = setup(monkeypatch, [])
    async def run(agent, prompt):
        await invoke(agent, "search_events", SEARCH)
        result = await invoke(agent, "get_weather", {"location": "Montreal", "date": "today", "local_time": None})
        assert result["status"] == "not_needed"
        return SimpleNamespace(final_output="No events found")
    monkeypatch.setattr(bot.Runner, "run", run)
    asyncio.run(bot.ask_agent(message))
    weather.assert_not_awaited()
    assert not any(call.args[0].startswith("🔧") for call in log.send.call_args_list)
    message.channel.send.assert_awaited_once_with("No verified events were found after the broad searches.")


def weather_criterion(person, preference, policy="restricted"):
    return Criterion(id=person, person=person, label="Weather", preference=preference,
                     source_message=preference, kind="weather", weather_policy=policy)


def test_each_person_gets_own_weather_match_and_safety_warnings_remain():
    row = event(1)
    criteria = [weather_criterion("Nawar", "Any weather is acceptable", "unrestricted"),
                weather_criterion("Akash", "Avoid rain")]
    prefs = {c.person: [c.source_message] for c in criteria}
    checks = [Assessment(event_url=row["url"], criterion_id="Akash", status="conflict",
                         reason="Rain conflicts with the current preference", evidence_field="weather", evidence_quote="Rain")]
    result = rank_candidates([row], prefs, criteria, checks, {weather_key("Montreal", row["date"], "19:00"): forecast()})
    assert result[0]["comparisons"]["Nawar"][0]["status"] == "match"
    assert result[0]["comparisons"]["Akash"][0]["status"] == "conflict"
    rendered = render_scorecards(result)
    assert "✅ Weather: No personal weather restriction; forecast is Rain" in rendered
    assert "❌ Weather: Rain conflicts" in rendered
    assert "Thunderstorms expected" in rendered


def test_out_of_range_is_unknown_even_for_unrestricted_preferences(monkeypatch):
    prefs = {"Nawar": "Any weather is acceptable"}
    message, log, weather = setup(monkeypatch, [event(1)], {"status": "unavailable"}, prefs)
    async def run(agent, prompt):
        await invoke(agent, "search_events", SEARCH)
        await invoke(agent, "rank_events", {"criteria": [weather_criterion("Nawar", prefs["Nawar"], "unrestricted").model_dump()],
                                            "assessments": []})
        return SimpleNamespace(final_output="Done")
    monkeypatch.setattr(bot.Runner, "run", run)
    asyncio.run(bot.ask_agent(message))
    text = message.channel.send.call_args.args[0]
    assert text.count("❓ Weather: Forecast is not available yet") == 2
    assert "°C" not in text and "Rain" not in text
    weather.assert_awaited_once()
    assert any("⚠️ unavailable" in c.args[0] for c in log.send.call_args_list)


def test_restricted_preference_requires_forecast_assessment_before_final_ranking(monkeypatch):
    prefs = {"Nawar": "Avoid rain"}
    message, _, weather = setup(monkeypatch, [event(1)], preferences=prefs)
    criterion = weather_criterion("Nawar", prefs["Nawar"])
    async def run(agent, prompt):
        await invoke(agent, "search_events", SEARCH)
        result = await invoke(agent, "rank_events", {"criteria": [criterion.model_dump()], "assessments": []})
        assert result["status"] == "needs_weather_assessment"
        assert "Rain" in result["forecasts"][0]["evidence"]
        check = Assessment(event_url=event(1)["url"], criterion_id="Nawar", status="conflict",
                           reason="Rain conflicts with the stated restriction", evidence_field="weather", evidence_quote="Rain")
        result = await invoke(agent, "rank_events", {"criteria": [criterion.model_dump()], "assessments": [check.model_dump()]})
        assert result["status"] == "ok"
        weather.assert_awaited_once()
        return SimpleNamespace(final_output="Done")
    monkeypatch.setattr(bot.Runner, "run", run)
    asyncio.run(bot.ask_agent(message))
    assert "❌ Weather:" in message.channel.send.call_args.args[0]


def test_same_day_hourly_payload_is_reused_for_different_verified_times():
    base = forecast()
    base["hourly_forecasts"] = [
        {"time": "2033-04-10T10:00", "temperature_c": 20, "precipitation_probability_percent": 0, "weather_code": 0},
        {"time": "2033-04-10T19:00", "temperature_c": 10, "precipitation_probability_percent": 90, "weather_code": 61},
    ]
    cache = {weather_key("Montreal", "2033-04-10", hour): base for hour in ("10:00", "19:00")}
    morning = forecast_for_event(event(1, time="10:00"), cache)
    evening = forecast_for_event(event(2, time="7:00 PM"), cache)
    assert morning["conditions"] == ["Clear sky"]
    assert evening["conditions"] == ["Rain or rain showers"]


def test_weather_preference_is_reloaded_after_discord_edit(monkeypatch):
    current = ["Avoid rain"]
    async def history(limit):
        yield SimpleNamespace(content=current[0], author=SimpleNamespace(bot=False, display_name="User"))
    guild = SimpleNamespace(text_channels=[SimpleNamespace(name="nawar-preferences", history=history)])
    async def read():
        prefs = {}
        await bot.get_preference_channel_context(guild, preference_records=prefs)
        return prefs
    before = asyncio.run(read())
    current[0] = "Any weather is acceptable"
    after = asyncio.run(read())
    assert before["Nawar"] == ["Avoid rain"]
    assert after["Nawar"] == ["Any weather is acceptable"]
    row = event(1)
    result = rank_candidates([row], after, [weather_criterion("Nawar", current[0], "unrestricted")], [],
                             {weather_key("Montreal", row["date"], "19:00"): forecast()})
    assert result[0]["comparisons"]["Nawar"][0]["status"] == "match"
