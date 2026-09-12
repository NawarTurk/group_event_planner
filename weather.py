"""Small, key-free Open-Meteo adapter for the planning agent."""

import asyncio
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import aiohttp

logger = logging.getLogger(__name__)


def describe_conditions(code: int) -> str:
    if code == 0:
        return "Clear sky"
    if code in (1, 2, 3):
        return {1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast"}[code]
    if code in (45, 48):
        return "Fog"
    if code in (56, 57, 66, 67):
        return "Freezing rain or drizzle"
    if code in (51, 53, 55):
        return "Drizzle"
    if code in (61, 63, 65, 80, 81, 82):
        return "Rain or rain showers"
    if code in (71, 73, 75, 77, 85, 86):
        return "Snow"
    if code in (95, 96, 99):
        return "Thunderstorm"
    return "Unknown conditions"


def planning_guidance(low: float, high: float, probability: float, codes: list[int]) -> dict:
    """Conservative comfort heuristics, not official weather alerts."""
    warnings = []
    if low < 15:
        warnings.append("Bring a jacket; cold weather expected." if low < 10 else "Bring a jacket.")
    if high >= 30:
        warnings.append("Heat expected; seek shade and bring water.")
    if probability >= 40 or any(51 <= c <= 67 or 80 <= c <= 82 for c in codes):
        warnings.append("Rain expected or possible; bring rain protection.")
    if any(c in (56, 57, 66, 67, 71, 73, 75, 77, 85, 86) for c in codes):
        warnings.append("Snow or ice expected; prefer indoor activities.")
    if any(c >= 95 for c in codes):
        warnings.append("Thunderstorms expected; prefer indoor activities.")
    if any(c in (45, 48) for c in codes):
        warnings.append("Fog may reduce visibility.")
    unpleasant = low < 10 or high >= 30 or probability >= 40 or any(c >= 45 for c in codes)
    return {"activity_suitability": "prefer_indoor" if unpleasant else "outdoor_suitable",
            "warnings": warnings}


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict) -> dict:
    async with session.get(url, params=params) as response:
        response.raise_for_status()
        return await response.json()


async def get_weather(location: str, date: str, local_time: str | None = None) -> dict:
    """Retrieve real current-hour or forecast weather from Open-Meteo.

    Use for direct weather questions and proactively for weather-dependent plans
    (picnics, hikes, walks, outdoor gatherings, festivals or trips). Interpret the
    meaning, including spelling mistakes; exact keywords are unnecessary. Skip
    unrelated requests and clearly indoor plans unless weather materially matters.
    Ask one concise question before calling if location or date is genuinely
    missing. Never answer live weather from memory or claim no live access when
    this tool is available. Montreal means Montreal, Quebec, Canada.
    Use returned Celsius temperatures, conditions, precipitation chance and
    warnings to guide indoor/outdoor choices; attribute to Open-Meteo. Summarize
    direct answers in at most two short sentences with at most one practical tip,
    unless the user asks for detail. If status is failed, say the lookup failed.

    Args:
        location: City with optional comma-separated full region/country names,
            e.g. 'Toronto, Ontario, Canada'. Ask the user if missing or ambiguous.
        date: YYYY-MM-DD, 'today'/'tomorrow', or 'now' for the current local hour.
            Must come from the user's request. Ask for ambiguous dates.
        local_time: Optional HH:MM in the location's local timezone (24-hour clock).
            Omit for a whole-day summary; do not invent a time.
    """
    logger.info("WEATHER TOOL CALLED: %s", location)
    location = location.strip()
    date = date.strip().lower()
    if location.casefold() in ("montreal", "montréal"):
        location = "Montreal, Quebec, Canada"
    if not location or not date:
        return {"status": "needs_clarification", "message": "Ask the user for both a location and date."}
    try:
        requested_time = time.fromisoformat(local_time) if local_time else None
        if requested_time and (requested_time.tzinfo or requested_time.second or requested_time.microsecond):
            raise ValueError
        # Validate before making network requests.
        if date not in ("today", "tomorrow", "now"):
            datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"status": "needs_clarification", "message": "Ask for a valid YYYY-MM-DD date and optional local HH:MM time."}

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            parts = [part.strip() for part in location.split(",") if part.strip()]
            places = await _get_json(session, "https://geocoding-api.open-meteo.com/v1/search",
                                     {"name": parts[0], "count": 10, "language": "en"})
            matches = places.get("results", [])
            for qualifier in parts[1:]:
                matches = [p for p in matches if qualifier.casefold() in {
                    str(p.get(k, "")).casefold() for k in ("admin1", "admin2", "country", "country_code")
                }]
            if len(matches) != 1:
                return {"status": "needs_clarification", "message": "Ask for a more specific city, full region and country.",
                        "candidates": [", ".join(filter(None, [p.get("name"), p.get("admin1"), p.get("country")])) for p in matches]}
            place = matches[0]
            timezone = place["timezone"]
            now = datetime.now(ZoneInfo(timezone))
            today = now.date()
            if date == "now":
                date = "today"
                requested_time = now.time().replace(second=0, microsecond=0)
            target = (today + timedelta(days=date.lower() == "tomorrow")
                      if date.lower() in ("today", "tomorrow") else datetime.strptime(date, "%Y-%m-%d").date())
            if not today <= target <= today + timedelta(days=15):
                return {"status": "unavailable", "message": "Forecasts support today through the next 15 days only. Ask for a date in that range or suggest checking closer to the date."}
            logger.info("Requesting Open-Meteo forecast for %s on %s", place["name"], target)
            data = await _get_json(session, "https://api.open-meteo.com/v1/forecast", {
                "latitude": place["latitude"], "longitude": place["longitude"],
                "timezone": timezone, "start_date": target.isoformat(), "end_date": target.isoformat(),
                "temperature_unit": "celsius",
                "hourly": "temperature_2m,precipitation_probability,weather_code",
            })
        hourly = data["hourly"]
        indices = [i for i, stamp in enumerate(hourly["time"])
                   if stamp[:10] == target.isoformat() and
                   (requested_time is None or datetime.fromisoformat(stamp).hour == requested_time.hour)]
        if len(indices) != (24 if requested_time is None else 1):
            raise ValueError("Incomplete forecast")
        temperatures = [hourly["temperature_2m"][i] for i in indices]
        probabilities = [hourly["precipitation_probability"][i] for i in indices]
        codes = [hourly["weather_code"][i] for i in indices]
        if any(v is None for v in temperatures + probabilities + codes):
            raise ValueError("Missing forecast values")
        low, high, probability = min(temperatures), max(temperatures), max(probabilities)
        return {"status": "ok", "source": "Open-Meteo", "source_url": "https://open-meteo.com/",
                "location": ", ".join(filter(None, [place["name"], place.get("admin1"), place.get("country")])),
                "timezone": timezone, "date": target.isoformat(),
                "period": hourly["time"][indices[0]] if requested_time else "whole day (including overnight)",
                "time_resolution": "hourly; supplied minutes use the containing hour",
                "temperature_c": {"min": low, "max": high},
                "precipitation_probability_percent": probability,
                "probability_scope": "maximum of selected hourly precipitation probabilities (rain or snow)",
                "conditions": list(dict.fromkeys(describe_conditions(c) for c in codes)),
                "hourly_forecasts": [
                    {"time": stamp, "temperature_c": hourly["temperature_2m"][i],
                     "precipitation_probability_percent": hourly["precipitation_probability"][i],
                     "weather_code": hourly["weather_code"][i]}
                    for i, stamp in enumerate(hourly["time"])
                    if all(hourly[field][i] is not None for field in
                           ("temperature_2m", "precipitation_probability", "weather_code"))
                ],
                **planning_guidance(low, high, probability, codes)}
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError, IndexError):
        logger.exception("Weather lookup failed")
        return {"status": "failed", "message": "The weather lookup failed. Please try again later. Do not invent a forecast."}
