"""Validate and render agent comparisons without choosing the event order."""

from typing import Literal

from pydantic import BaseModel
from event_weather import forecast_for_event, forecast_brief


class Criterion(BaseModel):
    id: str
    person: str
    label: str
    preference: str
    source_message: str
    kind: Literal["general", "weather"] = "general"
    category: Literal["Food", "Price", "Day/time", "Vibe/interests", "Setting", "Weather", "Other"] = "Other"
    weather_policy: Literal["restricted", "unrestricted"] = "restricted"


class Assessment(BaseModel):
    event_url: str
    criterion_id: str
    status: Literal["match", "conflict", "unknown"]
    reason: str
    evidence_field: str
    evidence_quote: str


def rank_candidates(
    candidates: list[dict], preferences: dict[str, list[str]],
    criteria: list[Criterion], assessments: list[Assessment], forecasts: dict | None = None,
) -> list[dict]:
    """Preserve the agent's order; unsupported or missing claims become unknown.

    Numeric counts are diagnostic only and never select or sort recommendations.
    """
    # Invalid or omitted extraction must not discard verified events.
    valid = []
    ids = set()
    covered = set()
    for criterion in criteria:
        if (criterion.id in ids or criterion.source_message not in preferences.get(criterion.person, [])
                or not criterion.preference.strip()
                or criterion.preference.casefold() not in criterion.source_message.casefold()):
            continue
        valid.append(criterion)
        ids.add(criterion.id)
        covered.add((criterion.person, criterion.source_message))
    for person, messages in preferences.items():
        for index, message in enumerate(messages):
            if (person, message) not in covered:
                valid.append(Criterion(id=f"missing:{person}:{index}", person=person,
                    label="Preference", preference=message, source_message=message))
    criteria = valid
    by_key = {}
    for item in assessments:
        key = (item.event_url, item.criterion_id)
        if key in by_key:
            continue
        by_key[key] = item
    ranked = []
    for event in candidates:
        comparisons = {person: [] for person in preferences}
        scores = {person: 0 for person in preferences}
        matches = conflicts = 0
        for criterion in criteria:
            if criterion.kind == "weather":
                continue  # aggregated into one real-forecast criterion per person below
            item = by_key.get((event["url"], criterion.id))
            status, reason = "unknown", "Not confirmed from available information"
            if item:
                value = event.get(item.evidence_field)
                supported = (item.evidence_field not in ("url", "source") and
                             isinstance(value, str) and bool(item.evidence_quote.strip()) and
                             item.evidence_quote.casefold() in value.casefold())
                if item.status == "unknown" or supported:
                    status, reason = item.status, item.reason
            matches += status == "match"
            conflicts += status == "conflict"
            scores[criterion.person] += (status == "match") - (status == "conflict")
            comparisons[criterion.person].append({"label": criterion.label, "preference": criterion.preference,
                                                  "status": status, "reason": reason, "category": criterion.category})
        if forecasts is not None:
            forecast = forecast_for_event(event, forecasts)
            brief = forecast_brief(forecast)
            for person in preferences:
                personal = [c for c in criteria if c.person == person and c.kind == "weather"]
                status = "unknown"
                if forecast.get("status") == "unavailable":
                    reason = "Forecast is not available yet"
                elif forecast.get("status") != "ok":
                    reason = "Weather lookup failed; forecast not confirmed"
                elif not personal:
                    reason = f"No personal weather preference provided; forecast is {brief}"
                elif all(c.weather_policy == "unrestricted" for c in personal):
                    status = "match"
                    reason = f"No personal weather restriction; forecast is {brief}"
                else:
                    decisions, reasons = [], []
                    for criterion in personal:
                        item = by_key.get((event["url"], criterion.id))
                        if criterion.weather_policy == "unrestricted":
                            decisions.append("match")
                        elif (item and item.evidence_field == "weather" and item.evidence_quote.strip()
                              and item.evidence_quote.casefold() in brief.casefold()):
                            decisions.append(item.status)
                            reasons.append(item.reason)
                        else:
                            decisions.append("unknown")
                    status = "conflict" if "conflict" in decisions else "unknown" if "unknown" in decisions else "match"
                    reason = "; ".join(reasons) if reasons else f"Compatibility not confirmed; forecast is {brief}"
                comparisons[person].append({"label": "Weather", "status": status, "reason": reason, "category": "Weather"})
                scores[person] += (status == "match") - (status == "conflict")
                matches += status == "match"
                conflicts += status == "conflict"
        net = sum(scores.values())
        balance = min(scores.values(), default=0)
        ranked.append({"event": event, "comparisons": comparisons, "score": net + balance,
                       "balance": balance, "matches": matches, "conflicts": conflicts})
        if forecasts is not None and forecast.get("status") == "ok":
            warnings = " ".join(forecast.get("warnings", []))
            ranked[-1]["weather_note"] = f"{brief}. {warnings} Source: Open-Meteo."
    return ranked


def render_scorecards(ranked: list[dict]) -> str:
    cards = []
    symbols = {"match": "✅", "conflict": "❌", "unknown": "❓"}
    for row in ranked:
        event = row["event"]
        lines = [f"🎟️ {event['title']}", f"📅 {event['date']} | {event.get('time') or 'Time not confirmed'}",
                 f"📍 {event.get('venue') or 'Venue not confirmed'} | {event['location']}", f"🔗 <{event['url']}>"]
        if row.get("weather_note"):
            lines.append(f"Weather: {row['weather_note']}")
        # These are display categories, not personal preferences or search filters.
        categories = ["Food", "Price", "Day/time", "Vibe/interests", "Setting", "Weather"]
        grouped = {person: {} for person in row["comparisons"]}
        for person, checks in row["comparisons"].items():
            for check in checks:
                category = check.get("category", "Other")
                if category == "Other":
                    category = check["label"]
                if category not in categories:
                    categories.append(category)
                grouped[person].setdefault(category, []).append(check)
        people = list(row["comparisons"])
        def cell(value: str) -> str:
            return " ".join(value.split()).replace("|", "\\|")
        lines.extend(["", "| Criterion | " + " | ".join(cell(p) for p in people) + " |",
                      "| --- | " + " | ".join("---" for _ in people) + " |"])
        for category in categories:
            values = []
            for person in people:
                checks = grouped[person].get(category, [])
                values.append("; ".join(f"{symbols[c['status']]} {cell(c['label'])}: {cell(c['reason'])}"
                                        for c in checks) if checks else "❓ Not confirmed")
            lines.append(f"| {cell(category)} | " + " | ".join(values) + " |")
        cards.append("\n".join(lines))
    return "\n\n".join(cards)
