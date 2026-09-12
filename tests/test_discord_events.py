"""Real SDK loop with scripted model decisions and mocked provider transport."""

import ast
import asyncio
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from agents import Model, ModelResponse, RunConfig, Runner, Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

import bot
import events


class EventModel(Model):
    def __init__(self, use_events=True, use_weather=False, raw_criterion=None):
        self.calls = 0
        self.use_events = use_events
        self.use_weather = use_weather
        self.raw_criterion = raw_criterion
        self.tool_outputs = []

    async def get_response(self, system_instructions, input, model_settings, tools, **kwargs):
        self.calls += 1
        assert [tool.name for tool in tools] == ["get_weather", "search_events", "rank_events"]
        assert model_settings.tool_choice in ("auto", None)
        assert model_settings.parallel_tool_calls is False
        if self.calls == 1:
            assert model_settings.tool_choice == "auto"
            prompt = input[0]["content"]
            assert "PRIVATE-NAWAR" in prompt and "PRIVATE-AKASH" in prompt
        else:
            self.tool_outputs = [item["output"] for item in input if isinstance(item, dict) and item.get("type") == "function_call_output"]
        if self.calls == 1 and self.use_events:
            output = [ResponseFunctionToolCall(
                type="function_call", name="search_events", id="fc_events", call_id="events_1",
                arguments=json.dumps({"location": self.raw_criterion or "Montreal", "date_or_timeframe": "2026-09-13", "max_results": 15}),
            )]
        elif self.calls == 2 and self.use_weather:
            assert "Jazz in the Park" in str(self.tool_outputs)
            output = [ResponseFunctionToolCall(
                type="function_call", name="get_weather", id="fc_weather", call_id="weather_1",
                arguments=json.dumps({"location": "Montreal", "date": "2026-09-13", "local_time": "19:00"}),
            )]
        else:
            output = [ResponseOutputMessage(
                type="message", role="assistant", id="answer", status="completed",
                content=[ResponseOutputText(type="output_text", text="A concise recommendation.", annotations=[])],
            )]
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError
        yield


def setup(monkeypatch, model):
    general = SimpleNamespace(name="general", send=AsyncMock())
    audit = SimpleNamespace(name="agent_log", send=AsyncMock())
    history_checks = []

    def pref_channel(name, content):
        async def history(limit):
            history_checks.append(name)
            yield SimpleNamespace(content=content, author=SimpleNamespace(bot=False, display_name=name))
        return SimpleNamespace(name=f"{name}-preferences", history=history)

    guild = SimpleNamespace(text_channels=[general, audit,
        pref_channel("nawar", "PRIVATE-NAWAR: I prefer live music and outdoor activities."),
        pref_channel("akash", "PRIVATE-AKASH: I prefer vegetarian food and low cost."),
    ])
    actual_run = Runner.run

    async def run(agent, prompt):
        assert history_checks == ["nawar", "akash"]
        assert audit.send.call_args_list[0].args[0] == "📚 Preferences loaded | nawar-preferences: ✅ 1 messages | akash-preferences: ✅ 1 messages"
        return await actual_run(agent, prompt, run_config=RunConfig(model=model, tracing_disabled=True))

    monkeypatch.setattr(bot.Runner, "run", run)
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(bot, "get_weather", AsyncMock(return_value={"status": "ok", "conditions": ["Clear sky"]}))
    monkeypatch.setattr(events, "_today", lambda: date(2026, 9, 12))
    monkeypatch.setenv("EXA_API_KEY", "mock-exa-credential")
    message = SimpleNamespace(content="Find an event we would both enjoy in Montreal tomorrow", guild=guild,
                              channel=general, author=SimpleNamespace(bot=False))
    return message, audit


def raw_result():
    text = "Jazz in the Park on September 13, 2026 in Montreal, Quebec, Canada."
    return {"id": "https://organizer.example/jazz", "url": "https://organizer.example/jazz", "text": text,
            "summary": json.dumps({"title": "Jazz in the Park", "date": "September 13, 2026",
                                   "location": "Montreal, Quebec, Canada", "date_evidence": text,
                                   "is_event": True, "matches_request": True, "source_type": "organizer"})}


@pytest.mark.parametrize("status,expected", [
    ("ok", "🔎 Exa search | Montreal, Quebec, Canada | 2026-09-13 | ✅ 1 candidates"),
    ("no_results", "🔎 Exa search | Montreal, Quebec, Canada | 2026-09-13 | ⚠️ no verified results"),
    ("failed", "🔎 Exa search | Montreal, Quebec, Canada | 2026-09-13 | ❌ failed"),
    ("not_configured", "🔎 Exa search | ❌ not configured"),
])
def test_real_adapter_execution_generates_exactly_one_accurate_exa_log(monkeypatch, caplog, status, expected):
    model = EventModel()
    message, audit = setup(monkeypatch, model)
    transport = AsyncMock(return_value={"results": [raw_result()] if status == "ok" else []})
    if status == "not_configured":
        monkeypatch.delenv("EXA_API_KEY")
    elif status == "failed":
        transport.side_effect = RuntimeError("mock-exa-credential PRIVATE-NAWAR")
    monkeypatch.setattr(events.AsyncExa, "async_request", transport)
    asyncio.run(bot.on_message(message))
    assert transport.await_count == (0 if status == "not_configured" else 1 if status == "failed" else 2)
    logs = [call.args[0] for call in audit.send.call_args_list]
    assert len(logs) == (3 if status == "ok" else 2)
    assert logs[1] == expected
    assert "PRIVATE" not in str(logs) + caplog.text
    assert "mock-exa-credential" not in str(logs) + caplog.text
    assert model.calls == (5 if status == "ok" else 2)
    assert status in str(model.tool_outputs)
    message.channel.send.assert_awaited_once()
    if status == "not_configured":
        message.channel.send.assert_awaited_once_with("Live event search is not configured yet.")
    if transport.await_count:
        payload = str(transport.call_args)
        assert "PRIVATE" not in payload
        assert "nawar" not in payload.casefold() and "akash" not in payload.casefold()


def test_agent_can_call_exa_then_weather_and_send_one_reply(monkeypatch):
    model = EventModel(use_weather=True)
    message, audit = setup(monkeypatch, model)
    transport = AsyncMock(return_value={"results": [raw_result()]})
    monkeypatch.setattr(events.AsyncExa, "async_request", transport)
    weather = AsyncMock(return_value={"status": "ok", "location": "Montreal, Quebec, Canada"})
    monkeypatch.setattr(bot, "get_weather", weather)
    asyncio.run(bot.ask_agent(message))
    assert transport.await_count == 2
    weather.assert_awaited_once_with("Montreal", "2026-09-13", "19:00")
    assert model.calls == 3
    assert len(model.tool_outputs) == 2
    assert [call.args[0].split()[0] for call in audit.send.call_args_list] == ["📚", "🔎", "🔧"]
    message.channel.send.assert_awaited_once()


def test_no_exa_execution_means_no_exa_audit(monkeypatch):
    model = EventModel(use_events=False)
    message, audit = setup(monkeypatch, model)
    transport = AsyncMock()
    monkeypatch.setattr(events.AsyncExa, "async_request", transport)
    asyncio.run(bot.ask_agent(message))
    transport.assert_not_awaited()
    audit.send.assert_awaited_once()  # preference audit only
    assert audit.send.call_args.args[0].startswith("📚")
    message.channel.send.assert_awaited_once()


def test_raw_preference_copy_is_blocked_before_exa(monkeypatch):
    model = EventModel(raw_criterion="PRIVATE-NAWAR: I prefer live music and outdoor activities.")
    message, audit = setup(monkeypatch, model)
    transport = AsyncMock()
    monkeypatch.setattr(events.AsyncExa, "async_request", transport)
    asyncio.run(bot.ask_agent(message))
    transport.assert_not_awaited()
    assert "needs_clarification" in str(model.tool_outputs)
    assert "PRIVATE" not in str(audit.send.call_args_list)
    message.channel.send.assert_awaited_once()


def test_exa_audit_failure_does_not_break_reply(monkeypatch):
    message, audit = setup(monkeypatch, EventModel())
    # Let preference audit succeed; only Exa completion audit fails.
    audit.send.side_effect = [None, RuntimeError("permission denied")]
    monkeypatch.setattr(events.AsyncExa, "async_request", AsyncMock(return_value={"results": [raw_result()]}))
    asyncio.run(bot.ask_agent(message))
    message.channel.send.assert_awaited_once()


def test_no_regex_intent_router_and_exact_sdk_pin():
    source = Path("bot.py").read_text()
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.Import) and any(alias.name == "re" for alias in node.names) for node in ast.walk(tree))
    assert "is_weather_request" not in source
    assert "exa-py==2.14.0" in Path("requirements.txt").read_text().splitlines()
    assert "EXA_API_KEY=" in Path(".env.example").read_text().splitlines()
    assert ".env" in Path(".gitignore").read_text().splitlines()
