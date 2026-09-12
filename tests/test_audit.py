from event_weather import normalize_forecast_request
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents.tool_context import ToolContext

import audit
import bot


def channels():
    general = SimpleNamespace(name="general", send=AsyncMock())
    log = SimpleNamespace(name="agent_log", send=AsyncMock())
    guild = SimpleNamespace(id=123, text_channels=[general, log])
    return guild, general, log


def test_startup_once_per_process_and_guild_only_in_audit(monkeypatch):
    guild, general, log = channels()
    other, other_general, other_log = channels()
    other.id = 456
    monkeypatch.setattr(bot, "client", SimpleNamespace(user="test-bot", guilds=[guild, other]))
    monkeypatch.setattr(bot, "startup_logged_guilds", set())

    async def ready_twice():
        await asyncio.gather(bot.on_ready(), bot.on_ready())

    asyncio.run(ready_twice())
    for channel in (log, other_log):
        channel.send.assert_awaited_once()
        assert channel.send.call_args.args[0] == f"🟢 Event Planner online | instance: {bot.INSTANCE_ID}"
    general.send.assert_not_called()
    other_general.send.assert_not_called()
    assert len(bot.INSTANCE_ID) == 6


@pytest.mark.parametrize("status,icon", [
    ("ok", "✅"), ("needs_clarification", "⚠️"),
    ("unavailable", "⚠️"), ("failed", "❌"),
])
def test_actual_tool_execution_logs_once_with_returned_status(monkeypatch, status, icon):
    guild, general, log = channels()
    result = {"status": status, "message": "private error detail must stay out of audit"}
    if status == "ok":
        result["location"] = "Montreal, Quebec, Canada"
    lookup = AsyncMock(return_value=result)
    monkeypatch.setattr(bot, "get_weather", lookup)
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "test-key")

    async def run(agent, prompt):
        assert agent.model_settings.tool_choice == "auto"
        assert agent.model_settings.parallel_tool_calls is False
        arguments = json.dumps({"location": "Montreal", "date": "now", "local_time": None})
        assert log.send.await_count == 1  # preference audit precedes the model
        assert log.send.call_args.args[0].startswith("📚 Preferences checked")
        returned = await agent.tools[0].on_invoke_tool(ToolContext(
            context=None, tool_name="get_weather", tool_call_id="weather", tool_arguments=arguments,
        ), arguments)
        assert returned == result
        lookup.assert_awaited_once_with(*normalize_forecast_request("Montreal", "now", None))
        assert log.send.await_count == 2  # exactly one additional tool audit
        return SimpleNamespace(final_output="User-facing answer")

    monkeypatch.setattr(bot.Runner, "run", run)
    asyncio.run(bot.ask_agent(SimpleNamespace(content="Check Montreal weather now", guild=guild, channel=general)))
    location = result.get("location", "Montreal, Quebec, Canada")
    _, day, hour = normalize_forecast_request("Montreal", "now")
    expected = f"🔧 get_weather | {location} | {day} {hour} | {icon} {status}"
    if status == "ok":
        expected += " | Open-Meteo"
    assert log.send.call_args.args[0] == expected
    assert log.send.call_args.kwargs["allowed_mentions"].everyone is False
    general.send.assert_awaited_once()


def test_no_tool_call_cannot_create_audit_from_model_answer(monkeypatch):
    guild, general, log = channels()
    fake_log = "🔧 get_weather | Montreal | now | ✅ ok | Open-Meteo"
    monkeypatch.setattr(bot.Runner, "run", AsyncMock(return_value=SimpleNamespace(final_output=fake_log)))
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "test-key")
    lookup = AsyncMock()
    monkeypatch.setattr(bot, "get_weather", lookup)
    asyncio.run(bot.ask_agent(SimpleNamespace(content="Suggest an indoor board game", guild=guild, channel=general)))
    lookup.assert_not_awaited()
    log.send.assert_awaited_once()
    assert log.send.call_args.args[0].startswith("📚 Preferences checked")
    general.send.assert_awaited_once_with(fake_log)


@pytest.mark.parametrize("missing", [True, False])
def test_audit_failure_does_not_break_tool_or_reply(monkeypatch, caplog, missing):
    guild, general, log = channels()
    if missing:
        guild.text_channels.remove(log)
    else:
        log.send.side_effect = RuntimeError("permission denied")
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(bot, "get_weather", AsyncMock(return_value={"status": "ok"}))

    async def run(agent, prompt):
        arguments = json.dumps({"location": "Montreal", "date": "now", "local_time": None})
        result = await agent.tools[0].on_invoke_tool(ToolContext(
            context=None, tool_name="get_weather", tool_call_id="weather", tool_arguments=arguments,
        ), arguments)
        assert result["status"] == "ok"
        return SimpleNamespace(final_output="Weather answer")

    monkeypatch.setattr(bot.Runner, "run", run)
    asyncio.run(bot.ask_agent(SimpleNamespace(content="Weather in Montreal now", guild=guild, channel=general)))
    general.send.assert_awaited_once_with("Weather answer")
    assert "Audit log skipped" in caplog.text


def test_audit_redacts_secrets_and_never_includes_full_result(monkeypatch):
    guild, _, log = channels()
    monkeypatch.setenv("OPENAI_API_KEY", "private-api-key")
    asyncio.run(audit.log_tool_completion(guild, "get_weather", "Montreal private-api-key", "now", "ok", "Open-Meteo"))
    assert "private-api-key" not in log.send.call_args.args[0]
