import asyncio
import json
from copy import deepcopy
from datetime import date
from unittest.mock import AsyncMock

import pytest

import events


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "mock-exa-credential")
    monkeypatch.setattr(events, "_today", lambda: date(2026, 9, 12))


def page(title="Jazz in the Park", url="https://organizer.example/jazz", scheduled="September 13, 2026"):
    evidence = f"{title}\nEvent date: {scheduled}"
    extracted = {
        "title": title, "date": scheduled, "time": "7:00 PM", "venue": "Example Park",
        "location": "Montreal, Quebec, Canada", "activities": "Live jazz outdoors",
        "food": "Vegetarian food available", "price": "$15",
        "date_evidence": evidence, "is_event": True, "matches_request": True, "source_type": "organizer",
    }
    text = evidence + "\n" + "\n".join(str(extracted.get(key, "")) for key in events.EVENT_FIELDS if key not in ("title", "date"))
    return {"id": url, "url": url, "title": title, "text": text,
            "publishedDate": "2026-09-12", "summary": json.dumps(extracted)}


def search():
    return events.search_events("Montreal", "2026-09-12/2026-09-14")


def test_missing_configuration_never_calls_sdk(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY")
    mock = AsyncMock()
    monkeypatch.setattr(events.AsyncExa, "async_request", mock)
    result = asyncio.run(search())
    assert result["status"] == "not_configured"
    assert result["events"] == []
    mock.assert_not_awaited()


def test_summary_schema_uses_exa_supported_scalar_types():
    # Exa's live validator rejects JSON Schema union type arrays.
    for field in events.EVENT_SCHEMA["properties"].values():
        assert isinstance(field["type"], str)


def test_empty_optional_summary_fields_become_unconfirmed(monkeypatch):
    row = page()
    summary = json.loads(row["summary"])
    summary["food"] = ""
    summary["price"] = ""
    row["summary"] = json.dumps(summary)
    monkeypatch.setattr(events.AsyncExa, "async_request", AsyncMock(return_value={"results": [row]}))
    event = asyncio.run(search())["events"][0]
    assert event["food"] is None
    assert event["price"] is None


def test_success_uses_pinned_sdk_syntax_and_verified_fields(monkeypatch, caplog):
    # Mock HTTP at the SDK boundary: the real SDK validates/camel-cases arguments
    # and builds its real response objects, without calling Exa.
    mock = AsyncMock(return_value={"results": [page()]})
    monkeypatch.setattr(events.AsyncExa, "async_request", mock)
    result = asyncio.run(search())
    assert result["status"] == "ok"
    assert result["source"] == "Exa"
    assert result["events"][0] == {
        "title": "Jazz in the Park", "date": "2026-09-13", "time": "7:00 PM",
        "venue": "Example Park", "location": "Montreal, Quebec, Canada",
        "activities": "Live jazz outdoors", "food": "Vegetarian food available", "price": "$15",
        "url": "https://organizer.example/jazz", "source": "organizer.example", "setting": None, "vibe": None,
    }
    assert mock.await_count == 3
    endpoint, payload = mock.call_args.args
    assert endpoint == "/search"
    assert payload["type"] == "auto"
    assert payload["contents"]["text"] == {"maxCharacters": 20000}
    assert payload["contents"]["summary"]["schema"] == events.EVENT_SCHEMA
    assert payload["contents"]["maxAgeHours"] == 0
    assert "startPublishedDate" not in payload
    assert "vegetarian food" not in payload["query"]
    for private in ("Nawar", "Akash", "mock-exa-credential", "nawar-preferences", "akash-preferences"):
        assert private not in json.dumps(payload)
        assert private not in caplog.text


@pytest.mark.parametrize("rows", [[], [page(scheduled="September 1, 2026")]])
def test_no_results_including_past_events(monkeypatch, rows):
    monkeypatch.setattr(events.AsyncExa, "async_request", AsyncMock(return_value={"results": rows}))
    assert asyncio.run(search())["status"] == "no_results"


@pytest.mark.parametrize("change", [
    {"date": "September 13"},  # no year; publication date cannot fill the gap
    {"date": "September 14, 2026"},  # made-up date absent from source
    {"is_event": False},
    {"location": "Toronto"},
])
def test_rejects_unverifiable_or_irrelevant_events(monkeypatch, change):
    row = page()
    summary = json.loads(row["summary"])
    summary.update(change)
    row["summary"] = json.dumps(summary)
    monkeypatch.setattr(events.AsyncExa, "async_request", AsyncMock(return_value={"results": [row]}))
    assert asyncio.run(search())["events"] == []


def test_optional_details_must_be_supported_and_missing_remains_null(monkeypatch):
    row = page()
    summary = json.loads(row["summary"])
    summary.update(food="Chicken and vegan burgers", price=None, activities="Games and fireworks")
    row["summary"] = json.dumps(summary)
    monkeypatch.setattr(events.AsyncExa, "async_request", AsyncMock(return_value={"results": [row]}))
    event = asyncio.run(search())["events"][0]
    assert event["food"] is None
    assert event["price"] is None
    assert event["activities"] is None


def test_deduplicates_and_prefers_official_source(monkeypatch):
    original = page()
    unofficial = page(url="https://blog.example/jazz")
    summary = json.loads(unofficial["summary"])
    summary["source_type"] = "other"
    unofficial["summary"] = json.dumps(summary)
    tracked = page(url=original["url"] + "?utm_source=example#tickets")
    other = page(title="Art in the Park", url="https://venue.example/art")
    malformed = deepcopy(other)
    malformed["summary"] = "not valid JSON"
    monkeypatch.setattr(events.AsyncExa, "async_request", AsyncMock(return_value={
        "results": [unofficial, original, tracked, malformed, other],
    }))
    rows = asyncio.run(search())["events"]
    assert [row["url"] for row in rows] == [original["url"], other["url"]]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://name:password@example.com", "not-a-url"])
def test_rejects_invalid_source_urls(monkeypatch, url):
    monkeypatch.setattr(events.AsyncExa, "async_request", AsyncMock(return_value={"results": [page(url=url)]}))
    assert asyncio.run(search())["events"] == []


def test_error_body_never_leaks_to_logs_or_results(monkeypatch, caplog):
    mock = AsyncMock(side_effect=RuntimeError("mock-exa-credential and private preference text"))
    monkeypatch.setattr(events.AsyncExa, "async_request", mock)
    result = asyncio.run(search())
    assert result["status"] == "failed"
    assert "RuntimeError" in caplog.text
    assert "mock-exa-credential" not in caplog.text + str(result)
    assert "private preference text" not in caplog.text + str(result)


def test_deadline_cancels_slow_search(monkeypatch):
    cancelled = []

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(events.AsyncExa, "async_request", stalled)
    monkeypatch.setattr(events, "SEARCH_TIMEOUT_SECONDS", 0.01)
    assert asyncio.run(search())["status"] == "failed"
    assert cancelled == [True, True, True]


@pytest.mark.parametrize("location,timeframe", [
    ("Montreal", "unclear timeframe"), ("Montreal", "2026-09-14/2026-09-12"),
    ("Montreal", "2020-09-12"), ("mock-exa-credential", "2026-09-13"),
])
def test_invalid_or_private_inputs_never_reach_exa(monkeypatch, location, timeframe):
    mock = AsyncMock()
    monkeypatch.setattr(events.AsyncExa, "async_request", mock)
    result = asyncio.run(events.search_events(location, timeframe))
    assert result["status"] == "needs_clarification"
    mock.assert_not_awaited()
