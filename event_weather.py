"""Per-request forecast identity, time selection and source-backed summaries."""

import unicodedata
from datetime import datetime

from weather import describe_conditions, planning_guidance


def weather_key(location: str, day: str) -> tuple[str, str]:
    city = " ".join("".join(c for c in unicodedata.normalize("NFKD", location.casefold())
                             if not unicodedata.combining(c)).split())
    if city in ("montreal", "montreal, quebec, canada"):
        city = "montreal, quebec, canada"
    return city, day


def event_time(event: dict) -> str | None:
    value = event.get("time")
    if not value:
        return None
    for fmt in ("%H:%M", "%I:%M %p", "%I %p", "%I:%M%p", "%I%p"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def forecast_for_event(event: dict, cache: dict) -> dict:
    forecast = cache.get(weather_key(event["location"], event["date"]), {})
    rows = forecast.get("hourly_forecasts", [])
    if forecast.get("status") != "ok" or not rows:
        return forecast
    local_time = event_time(event)
    selected = [row for row in rows if row["time"][:10] == event["date"] and
                (local_time is None or row["time"][11:13] == local_time[:2])]
    if not selected:
        return {"status": "unavailable"}
    low = min(row["temperature_c"] for row in selected)
    high = max(row["temperature_c"] for row in selected)
    chance = max(row["precipitation_probability_percent"] for row in selected)
    codes = [row["weather_code"] for row in selected]
    return {**forecast, "temperature_c": {"min": low, "max": high},
            "precipitation_probability_percent": chance,
            "conditions": list(dict.fromkeys(describe_conditions(code) for code in codes)),
            "period": selected[0]["time"] if local_time else "whole day",
            **planning_guidance(low, high, chance, codes)}


def forecast_brief(forecast: dict) -> str:
    conditions = ", ".join(forecast.get("conditions", [])) or "conditions not confirmed"
    temperature = forecast.get("temperature_c")
    if temperature:
        conditions += f"; {temperature['min']}–{temperature['max']}°C"
    probability = forecast.get("precipitation_probability_percent")
    if probability is not None:
        conditions += f"; {probability}% peak hourly precipitation chance"
    return conditions
