import ast
import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from agents.tool_context import ToolContext

import bot
import events
from ranking import Assessment, Criterion, rank_candidates, render_scorecards


@pytest.mark.parametrize("day", [date(2031, 3, 5), date(2032, 12, 31)])
def test_phrases_resolve_from_runtime_each_time(monkeypatch, day):
    monkeypatch.setattr(events, "runtime_now", lambda: datetime.combine(day, datetime.min.time(), ZoneInfo("America/Toronto")))
    monkeypatch.setenv("EVENT_SEARCH_DAYS", "9")
    assert events._date_window("today") == (day, day)
    assert events._date_window("tomorrow") == (day + timedelta(days=1),) * 2
    assert events._date_window("") == (day, day + timedelta(days=8))
    saturday = day + timedelta(days=5 - day.weekday())
    assert events._date_window("this weekend") == (max(day, saturday), saturday + timedelta(days=1))
    monday = day + timedelta(days=7 - day.weekday())
    assert events._date_window("next week") == (monday, monday + timedelta(days=6))


def test_clock_uses_configured_timezone(monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2031, 3, 5, 2, tzinfo=ZoneInfo("UTC")).astimezone(tz)
    monkeypatch.setattr(events, "datetime", Frozen)
    monkeypatch.setenv("LOCAL_TIMEZONE", "America/Toronto")
    assert events._today() == date(2031, 3, 4)
    monkeypatch.setenv("LOCAL_TIMEZONE", "Asia/Tokyo")
    assert events._today() == date(2031, 3, 5)


def criterion(id, person, label, preference):
    return Criterion(id=id, person=person, label=label, preference=preference, source_message=preference)


def event(number):
    return {"title": f"Event {number}", "date": "2031-03-06", "time": None, "venue": None,
            "location": "Test City", "activities": "Dancing", "food": None, "price": "$20",
            "url": f"https://venue.example/{number}", "source": "venue.example"}


def assessment(row, criterion_id, status, field="activities", quote="Dancing"):
    return Assessment(event_url=row["url"], criterion_id=criterion_id, status=status,
                      reason=f"{status} against the stated criterion", evidence_field=field, evidence_quote=quote)


def test_or_scoring_each_user_unknown_fields_and_balanced_top_three():
    criteria = [criterion("a", "Person A", "Interests", "Enjoys dancing"),
                criterion("b", "Person A", "Food", "Needs a meal"),
                criterion("c", "Person B", "Interests", "Prefers sitting quietly")]
    preferences = {"Person A": [c.source_message for c in criteria if c.person == "Person A"],
                   "Person B": [c.source_message for c in criteria if c.person == "Person B"]}
    rows = [event(i) for i in range(4)]
    checks = [assessment(rows[0], "a", "match"), assessment(rows[0], "c", "conflict"),
              assessment(rows[1], "a", "match"), assessment(rows[1], "c", "match"),
              assessment(rows[2], "a", "match"), assessment(rows[3], "a", "conflict"),
              assessment(rows[0], "b", "match", "food", "meal")]
    ranked = rank_candidates(rows, preferences, criteria, checks)
    assert [r["event"]["url"] for r in ranked] == [rows[1]["url"], rows[2]["url"], rows[0]["url"]]
    traded = ranked[2]
    assert traded["comparisons"]["Person A"][0]["status"] == "match"  # one match is enough
    assert traded["comparisons"]["Person B"][0]["status"] == "conflict"
    assert traded["comparisons"]["Person A"][1]["status"] == "unknown"
    assert traded["matches"] == 1 and traded["conflicts"] == 1
    assert "❓ Food:" in render_scorecards(ranked)
    assert "✅ Interests:" in render_scorecards(ranked)
    assert all(len(r["comparisons"]["Person A"]) == 2 for r in ranked)


def test_unknown_does_not_change_score_and_missing_assessment_is_displayed():
    row = event(1)
    crit = criterion("p", "Person", "Price", "Budget preference")
    ranked = rank_candidates([row], {"Person": [crit.source_message]}, [crit], [])
    assert ranked[0]["score"] == 0
    assert ranked[0]["comparisons"]["Person"][0]["status"] == "unknown"


def test_cannot_invent_preferences_or_omit_source_messages():
    crit = criterion("a", "Person", "Food", "Old preference")
    with pytest.raises(ValueError):
        rank_candidates([event(1)], {"Person": ["Updated preference"]}, [crit], [])
    with pytest.raises(ValueError):
        rank_candidates([event(1)], {"Person": ["Updated preference"]}, [], [])


def test_dynamic_discord_edit_changes_next_ranking(monkeypatch):
    current = ["Enjoys dancing"]
    async def history(limit):
        assert limit is None
        yield SimpleNamespace(content=current[0], author=SimpleNamespace(bot=False, display_name="Person"))
    guild = SimpleNamespace(text_channels=[SimpleNamespace(name="nawar-preferences", history=history)])
    async def read():
        records = {}
        await bot.get_preference_channel_context(guild, preference_records=records)
        return records
    first = asyncio.run(read())
    current[0] = "Avoids dancing"
    second = asyncio.run(read())
    assert first["Nawar"] == ["Enjoys dancing"]
    assert second["Nawar"] == ["Avoids dancing"]
    row = event(1)
    a = rank_candidates([row], first, [criterion("a", "Nawar", "Interests", first["Nawar"][0])], [assessment(row, "a", "match")])
    b = rank_candidates([row], second, [criterion("a", "Nawar", "Interests", second["Nawar"][0])], [assessment(row, "a", "conflict")])
    assert a[0]["score"] > b[0]["score"]


def test_fallback_is_once_and_contains_only_city_and_runtime_window(monkeypatch):
    day = date(2031, 3, 5)
    monkeypatch.setattr(events, "_today", lambda: day)
    monkeypatch.setenv("EXA_API_KEY", "mock-key")
    monkeypatch.setenv("DEFAULT_EVENT_LOCATION", "Example City")
    monkeypatch.setenv("EVENT_SEARCH_DAYS", "7")
    transport = AsyncMock(return_value={"results": []})
    monkeypatch.setattr(events.AsyncExa, "async_request", transport)
    result = asyncio.run(events.search_events())
    assert result["date_or_timeframe"] == f"{day}/{day + timedelta(days=6)}"
    assert result["location"] == "Example City"
    assert result["search_attempts"] == 2
    assert transport.await_count == 2
    fallback = transport.call_args_list[1].args[1]["query"]
    assert fallback == f"Events in Example City from {day} through {day + timedelta(days=6)}"
    assert "Interests:" not in fallback and "Food:" not in fallback


def test_past_and_nonlocal_candidates_are_excluded(monkeypatch):
    monkeypatch.setattr(events, "_today", lambda: date(2031, 3, 5))
    rows = []
    for scheduled, city in (("2031-03-04", "Example City"), ("2031-03-06", "Other City"), ("2031-03-06", "Example City")):
        text = f"An event on {scheduled} in {city}"
        summary = {"title": "An event", "date": scheduled, "location": city, "date_evidence": text,
                   "is_event": True, "matches_request": True}
        rows.append(SimpleNamespace(url=f"https://example.com/{scheduled}/{city.replace(' ', '-')}", text=text, summary=summary))
    verified = events._verify_results(rows, "Example City", date(2031, 3, 5), date(2031, 3, 10), 15)
    assert len(verified) == 1
    assert verified[0]["date"] == "2031-03-06"
    assert verified[0]["food"] is None and verified[0]["price"] is None


def test_ranking_tool_updates_real_audit_and_sends_all_checks(monkeypatch):
    log_message = SimpleNamespace(edit=AsyncMock())
    audit = SimpleNamespace(name="agent_log", send=AsyncMock(return_value=log_message))
    general = SimpleNamespace(name="general", send=AsyncMock())
    async def history(limit):
        yield SimpleNamespace(content="Enjoys dancing", author=SimpleNamespace(bot=False, display_name="Person"))
    guild = SimpleNamespace(text_channels=[audit, general, SimpleNamespace(name="nawar-preferences", history=history)])
    rows = [event(i) for i in range(10)]
    monkeypatch.setattr(bot, "search_events", AsyncMock(return_value={"status": "ok", "events": rows,
                        "location": "Test City", "date_or_timeframe": "calculated range"}))
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(bot, "get_weather", AsyncMock(return_value={"status": "ok", "conditions": ["Clear sky"]}))
    async def run(agent, prompt):
        async def invoke(name, args):
            encoded = json.dumps(args)
            tool = next(t for t in agent.tools if t.name == name)
            return await tool.on_invoke_tool(ToolContext(context=None, tool_name=name, tool_call_id=name, tool_arguments=encoded), encoded)
        await invoke("search_events", {"location": "", "date_or_timeframe": "", "max_results": 15})
        result = await invoke("rank_events", {"criteria": [criterion("a", "Nawar", "Interests", "Enjoys dancing").model_dump()],
                                            "assessments": [assessment(row, "a", "match").model_dump() for row in rows]})
        assert result["status"] == "ok"
        assert len(result["ranked"]) == 3
        return SimpleNamespace(final_output="Model text is not the scorecard authority")
    monkeypatch.setattr(bot.Runner, "run", run)
    asyncio.run(bot.ask_agent(SimpleNamespace(content="Find events", channel=general, guild=guild)))
    log_message.edit.assert_awaited_once()
    assert "10 candidates, 3 ranked" in log_message.edit.call_args.kwargs["content"]
    general.send.assert_awaited_once()
    assert general.send.call_args.args[0].count("✅ Interests:") == 3


def test_production_has_no_fixed_calendar_dates_or_preference_fixtures():
    for filename in ("bot.py", "events.py", "ranking.py"):
        tree = ast.parse(Path(filename).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                try:
                    date.fromisoformat(node.value)
                except ValueError:
                    continue
                pytest.fail(f"Fixed production date in {filename}")
        assert "PRIVATE-NAWAR" not in Path(filename).read_text()
    schema = __import__("inspect").signature(events.search_events)
    assert set(schema.parameters) == {"location", "date_or_timeframe", "max_results"}
