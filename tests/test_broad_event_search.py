import asyncio
import calendar
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import bot
import events
import weather
from test_required_event_weather import setup, invoke


def page(day, title='Community gathering', **optional):
    data = {'title': title, 'date': day.isoformat(), 'location': 'Montreal, Quebec, Canada',
            'is_event': True, 'matches_request': False, 'source_type': 'organizer', **optional}
    return {'id': f'https://venue.example/{title.replace(" ", "-")}',
            'url': f'https://venue.example/{title.replace(" ", "-")}',
            'text': '\n'.join(str(v) for v in data.values()), 'summary': json.dumps(data)}


@pytest.mark.parametrize('today,phrase,start,end', [
    (date(2034, 9, 12), 'September', date(2034, 9, 12), date(2034, 9, 30)),
    (date(2034, 8, 12), 'in September', date(2034, 9, 1), date(2034, 9, 30)),
    (date(2034, 10, 12), 'September', date(2035, 9, 1), date(2035, 9, 30)),
    (date(2036, 1, 1), 'February', date(2036, 2, 1), date(2036, 2, 29)),
    (date(2034, 9, 12), 'September 2035', date(2035, 9, 1), date(2035, 9, 30)),
])
def test_months_are_dynamic_and_never_need_specific_date(monkeypatch, today, phrase, start, end):
    monkeypatch.setattr(events, '_today', lambda: today)
    assert events._date_window(phrase) == (start, end)


def test_week_and_empty_window_are_runtime_based(monkeypatch):
    today = date(2034, 9, 12)
    monkeypatch.setattr(events, '_today', lambda: today)
    monkeypatch.delenv('EVENT_SEARCH_DAYS', raising=False)
    assert events._date_window('') == (today, today + timedelta(days=29))
    assert events._date_window('this week') == (today, today + timedelta(days=6-today.weekday()))
    monday = today + timedelta(days=7-today.weekday())
    assert events._date_window('next week') == (monday, monday + timedelta(days=6))
    assert events._date_window('September 14, 2034 to September 18, 2034') == (date(2034, 9, 14), date(2034, 9, 18))


@pytest.mark.parametrize('city,timezone,expected', [
    ('Tokyo, Japan', 'Asia/Tokyo', date(2034, 10, 1)),
    ('Vancouver, Canada', 'America/Vancouver', date(2034, 9, 30)),
])
def test_event_city_timezone_controls_range(monkeypatch, city, timezone, expected):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2034, 10, 1, 0, 30, tzinfo=ZoneInfo('UTC')).astimezone(tz)
    monkeypatch.setattr(events, 'datetime', Frozen)
    monkeypatch.delenv('EXA_API_KEY', raising=False)
    monkeypatch.setenv('DEFAULT_EVENT_LOCATION', 'Montreal, Quebec, Canada')
    monkeypatch.setattr(weather, '_get_json', AsyncMock(return_value={'results': [
        {'name': city.split(',')[0], 'country': city.split(', ')[1], 'timezone': timezone}]}))
    result = asyncio.run(events.search_events(city, 'today'))
    assert result['date_or_timeframe'] == expected.isoformat()
    assert result['timezone'] == timezone


def test_failed_initial_search_continues_and_keeps_imperfect_unknown_events(monkeypatch):
    today = date(2034, 9, 12)
    monkeypatch.setattr(events, '_today', lambda: today)
    monkeypatch.setenv('EXA_API_KEY', 'mock-key')
    row = page(today + timedelta(days=1))
    transport = AsyncMock(side_effect=[RuntimeError('transient'), {'results': []}, {'results': [row, row]}])
    monkeypatch.setattr(events.AsyncExa, 'async_request', transport)
    result = asyncio.run(events.search_events('Montreal', 'September'))
    assert result['status'] == 'ok' and len(result['events']) == 1
    assert result['retrieved_count'] == 2 and result['deduplicated_count'] == 1
    assert result['events'][0]['food'] is None and result['events'][0]['price'] is None
    assert result['events'][0]['vibe'] is None and result['events'][0]['setting'] is None
    assert [c['status'] for c in result['calls']] == ['failed', 'ok', 'ok']
    assert transport.await_count == 3
    assert ' OR ' in transport.call_args.args[1]['query']
    assert 'preferences' not in transport.call_args.args[1]['query']


@pytest.mark.parametrize('user_request,timeframe', [('Find events in Montreal in September', 'September'), ('Find events', '')])
def test_discord_month_or_default_search_returns_event_without_date_question(monkeypatch, user_request, timeframe):
    today = date(2034, 9, 12)
    monkeypatch.setattr(events, '_today', lambda: today)
    monkeypatch.setenv('DEFAULT_EVENT_LOCATION', 'Montreal, Quebec, Canada')
    monkeypatch.delenv('EVENT_SEARCH_DAYS', raising=False)
    monkeypatch.setenv('EXA_API_KEY', 'mock-key')
    message, log, lookup = setup(monkeypatch, [], preferences={'Nawar': 'Enjoy music', 'Akash': 'Low budget'})
    message.content = user_request
    monkeypatch.setattr(bot, 'search_events', events.search_events)
    monkeypatch.setattr(events.AsyncExa, 'async_request', AsyncMock(return_value={'results': [page(today+timedelta(days=1))]}))
    async def run(agent, prompt):
        assert 'never ask for a specific date when a month' in agent.instructions
        result = await invoke(agent, 'search_events', {'location': '', 'date_or_timeframe': timeframe, 'max_results': 15})
        assert result['status'] == 'ok'
        assert result['location'] == 'Montreal, Quebec, Canada'
        expected_end = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1]) if timeframe else today+timedelta(days=29)
        assert result['date_or_timeframe'] == f'{today}/{expected_end}'
        await invoke(agent, 'rank_events', {'criteria': [], 'assessments': []})
        return SimpleNamespace(final_output='Done')
    monkeypatch.setattr(bot.Runner, 'run', run)
    asyncio.run(bot.ask_agent(message))
    message.channel.send.assert_awaited_once()
    text = message.channel.send.call_args.args[0]
    assert 'Community gathering' in text and 'https://venue.example/' in text
    assert 'specific date' not in text and '❓' in text
    records = [c.args[0] for c in log.send.call_args_list]
    assert len([r for r in records if r.startswith('🔎 Exa call')]) == 3
    assert any('3 retrieved | 1 usable after deduplication' in r for r in records)
    assert any('1 candidates | 1 shown' in r for r in records)
