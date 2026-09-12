import asyncio
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from agents import function_tool

import weather


@pytest.mark.parametrize("low,high,chance,codes,expected,warning", [
    (20, 24, 10, [0, 2], "outdoor_suitable", None),
    (12, 18, 0, [1], "outdoor_suitable", "jacket"),
    (5, 8, 0, [0], "prefer_indoor", "jacket"),
    (18, 20, 70, [61], "prefer_indoor", "Rain"),
    (-3, 0, 30, [73], "prefer_indoor", "Snow"),
    (30, 35, 0, [0], "prefer_indoor", "Heat"),
    (20, 22, 10, [95], "prefer_indoor", "Thunderstorms"),
])
def test_guidance(low, high, chance, codes, expected, warning):
    result = weather.planning_guidance(low, high, chance, codes)
    assert result["activity_suitability"] == expected
    if warning:
        assert warning in " ".join(result["warnings"])


def fixture_responses():
    day = datetime.now(ZoneInfo("America/Toronto")).date().isoformat()
    return [
        {"results": [{"name": "Toronto", "admin1": "Ontario", "country": "Canada",
                      "latitude": 43.7, "longitude": -79.4, "timezone": "America/Toronto"}]},
        {"hourly": {"time": [f"{day}T{hour:02}:00" for hour in range(24)],
                    "temperature_2m": [5] * 18 + [21] * 6,
                    "precipitation_probability": [60] * 18 + [5] * 6,
                    "weather_code": [61] * 18 + [0] * 6}},
    ]


def test_hour_selection_and_sdk_schema(monkeypatch):
    mock = AsyncMock(side_effect=fixture_responses())
    monkeypatch.setattr(weather, "_get_json", mock)
    result = asyncio.run(weather.get_weather("Toronto, Ontario, Canada", "today", "18:30"))
    assert result["status"] == "ok"
    assert result["temperature_c"] == {"min": 21, "max": 21}
    assert result["precipitation_probability_percent"] == 5
    assert result["activity_suitability"] == "outdoor_suitable"
    assert result["period"].endswith("18:00")
    assert result["timezone"] == "America/Toronto"
    tool = function_tool(weather.get_weather)
    assert tool.name == "get_weather"
    assert set(tool.params_json_schema["properties"]) == {"location", "date", "local_time"}


def test_daily_summary(monkeypatch):
    monkeypatch.setattr(weather, "_get_json", AsyncMock(side_effect=fixture_responses()))
    result = asyncio.run(weather.get_weather("Toronto, Canada", "today"))
    assert result["temperature_c"] == {"min": 5, "max": 21}
    assert result["precipitation_probability_percent"] == 60
    assert result["activity_suitability"] == "prefer_indoor"


@pytest.mark.parametrize("location,day,local_time", [
    ("", "today", None), ("Toronto", "", None),
    ("Toronto", "2026-02-30", None), ("Toronto", "today", "25:00"),
])
def test_invalid_input_does_not_fetch(monkeypatch, location, day, local_time):
    mock = AsyncMock()
    monkeypatch.setattr(weather, "_get_json", mock)
    assert asyncio.run(weather.get_weather(location, day, local_time))["status"] == "needs_clarification"
    mock.assert_not_called()


def test_ambiguous_location_does_not_fetch_forecast(monkeypatch):
    places = fixture_responses()[0]
    places["results"] *= 2
    mock = AsyncMock(return_value=places)
    monkeypatch.setattr(weather, "_get_json", mock)
    assert asyncio.run(weather.get_weather("Toronto", "today"))["status"] == "needs_clarification"
    assert mock.await_count == 1


@pytest.mark.parametrize("day", ["2000-01-01", "2099-01-01"])
def test_out_of_range(monkeypatch, day):
    mock = AsyncMock(return_value=fixture_responses()[0])
    monkeypatch.setattr(weather, "_get_json", mock)
    assert asyncio.run(weather.get_weather("Toronto, Canada", day))["status"] == "unavailable"
    assert mock.await_count == 1


def test_timeout(monkeypatch):
    monkeypatch.setattr(weather, "_get_json", AsyncMock(side_effect=asyncio.TimeoutError))
    assert asyncio.run(weather.get_weather("Toronto", "today"))["status"] == "failed"


def test_missing_forecast_values(monkeypatch):
    responses = fixture_responses()
    responses[1]["hourly"]["temperature_2m"][18] = None
    monkeypatch.setattr(weather, "_get_json", AsyncMock(side_effect=responses))
    assert asyncio.run(weather.get_weather("Toronto", "today", "18:00"))["status"] == "failed"
