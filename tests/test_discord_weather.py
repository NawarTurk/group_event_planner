"""Exercise the real SDK tool loop with a deterministic model and mocked HTTP."""

import asyncio
import json
import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from agents import Model, ModelResponse, RunConfig, Runner, Usage
from agents.tool_context import ToolContext
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

import bot
import weather


class WeatherModel(Model):
    def __init__(self, user_text="What is the weather in Montreal now?", date="now", use_tool=True):
        self.calls = 0
        self.tool_result = None
        self.user_text = user_text
        self.date = date
        self.use_tool = use_tool

    async def get_response(self, system_instructions, input, model_settings, tools, **kwargs):
        self.calls += 1
        assert [tool.name for tool in tools] == ["get_weather", "search_events", "rank_events"]
        assert model_settings.parallel_tool_calls is False
        if self.calls == 1:
            assert any(item.get("content") == self.user_text for item in input)
            assert model_settings.tool_choice == "auto"
        else:
            # The SDK normally resets this to its default after tool execution.
            assert model_settings.tool_choice in (None, "auto")
        if self.calls == 1 and self.use_tool:
            output = [ResponseFunctionToolCall(
                id="fc_weather", call_id="weather_1", type="function_call", name="get_weather",
                arguments=json.dumps({"location": "Montreal", "date": self.date, "local_time": None}),
            )]
        else:
            assert self.calls == (2 if self.use_tool else 1)
            if self.use_tool:
                result = next(item for item in input if isinstance(item, dict) and item.get("type") == "function_call_output")
                assert result["call_id"] == "weather_1"
                self.tool_result = result["output"]
            output = [ResponseOutputMessage(
                id="msg_weather", type="message", role="assistant", status="completed",
                content=[ResponseOutputText(type="output_text", text="In Montreal it is 20°C and clear, according to Open-Meteo.", annotations=[])],
            )]
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError
        yield


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("user_text", ["What is the weather in Montreal now?", "waht is the wether in montrel now"])
def test_montreal_now_calls_weather_once_and_sends_one_reply(monkeypatch, caplog, fails, user_text):
    # Scripted model choices test the SDK plumbing, not live model comprehension.
    model = WeatherModel(user_text=user_text)
    actual_run = Runner.run

    async def run(agent, prompt):
        assert agent.reset_tool_choice is True
        return await actual_run(agent, prompt, run_config=RunConfig(model=model, tracing_disabled=True))

    monkeypatch.setattr(bot.Runner, "run", run)
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "test-key")
    lookup = AsyncMock(wraps=weather.get_weather)
    monkeypatch.setattr(bot, "get_weather", lookup)
    # Preserve the tool description when wrapping the adapter.
    lookup.__doc__ = weather.get_weather.__doc__
    now = datetime.now(ZoneInfo("America/Toronto"))
    place = {"name": "Montréal", "admin1": "Quebec", "country": "Canada",
             "timezone": "America/Toronto", "latitude": 45.5, "longitude": -73.6}
    http = AsyncMock(side_effect=RuntimeError("unused"))
    if fails:
        http.side_effect = weather.aiohttp.ClientConnectionError("test connection refused")
    else:
        http.side_effect = [
            {"results": [place, {**place, "country": "France"}]},
            {"hourly": {"time": [f"{now.date()}T{hour:02}:00" for hour in range(24)],
                        "temperature_2m": [20] * 24, "precipitation_probability": [5] * 24,
                        "weather_code": [0] * 24}},
        ]
    monkeypatch.setattr(weather, "_get_json", http)
    message = SimpleNamespace(content=user_text, guild=None,
                              author=SimpleNamespace(bot=False),
                              channel=SimpleNamespace(name="general", send=AsyncMock()))
    with caplog.at_level(logging.INFO):
        asyncio.run(bot.on_message(message))
    lookup.assert_awaited_once_with("Montreal", "now", None)
    message.channel.send.assert_awaited_once()
    assert model.calls == 2  # tool call, then natural-language response using its output
    assert "WEATHER TOOL CALLED: Montreal" in caplog.text
    if fails:
        assert "test connection refused" in caplog.text
        assert "weather lookup failed" in message.channel.send.call_args.args[0]
        assert "failed" in model.tool_result
    else:
        assert "Quebec" in model.tool_result
        assert f"T{now.hour:02}:00" in model.tool_result
        assert "20°C" in message.channel.send.call_args.args[0]


@pytest.mark.parametrize("user_text,use_tool,date", [
    ("Help us plan a picnic in Montreal tomorrow.", True, "tomorrow"),
    ("Suggest an indoor board game.", False, "now"),
    ("Help us plan a picnic tomorrow.", False, "tomorrow"),
])
def test_model_can_choose_tool_or_answer_without_it(monkeypatch, user_text, use_tool, date):
    model = WeatherModel(user_text=user_text, date=date, use_tool=use_tool)
    actual_run = Runner.run

    async def run(agent, prompt):
        assert agent.model_settings.tool_choice == "auto"
        assert agent.reset_tool_choice is True
        return await actual_run(agent, prompt, run_config=RunConfig(model=model, tracing_disabled=True))

    monkeypatch.setattr(bot.Runner, "run", run)
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "test-key")
    lookup = AsyncMock(return_value={"status": "ok", "temperature_c": {"min": 20, "max": 20}})
    lookup.__doc__ = weather.get_weather.__doc__
    monkeypatch.setattr(bot, "get_weather", lookup)
    message = SimpleNamespace(content=user_text, guild=None,
                              channel=SimpleNamespace(name="general", send=AsyncMock()))
    asyncio.run(bot.ask_agent(message))
    assert lookup.await_count == int(use_tool)
    if use_tool:
        lookup.assert_awaited_once_with("Montreal", date, None)
    message.channel.send.assert_awaited_once()


def test_wrapper_executes_each_requested_lookup_without_mutating_settings(monkeypatch):
    lookup = AsyncMock(side_effect=[{"status": "ok", "date": "today"}, RuntimeError("test adapter error")])
    lookup.__doc__ = weather.get_weather.__doc__
    monkeypatch.setattr(bot, "get_weather", lookup)
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "test-key")

    async def run(agent, prompt):
        tool = agent.tools[0]
        for date in ("today", "tomorrow"):
            arguments = json.dumps({
                "location": "Montreal", "date": date, "local_time": None,
            })
            result = await tool.on_invoke_tool(ToolContext(
                context=None, tool_name="get_weather", tool_call_id=date, tool_arguments=arguments,
            ), arguments)
            assert result["status"] == ("ok" if date == "today" else "failed")
            assert agent.model_settings.tool_choice == "auto"
            assert agent.reset_tool_choice is True
        return SimpleNamespace(final_output="The weather lookup failed.")

    monkeypatch.setattr(bot.Runner, "run", run)
    message = SimpleNamespace(content="Compare today and tomorrow in Montreal", guild=None,
                              channel=SimpleNamespace(name="general", send=AsyncMock()))
    asyncio.run(bot.ask_agent(message))
    assert lookup.await_count == 2
    message.channel.send.assert_awaited_once_with("The weather lookup failed. Please try again later.")
