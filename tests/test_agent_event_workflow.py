"""Mocked agent submissions prove ownership, graceful partial output and cache identity."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import bot
import event_weather
import events
from test_required_event_weather import setup, event, invoke, SEARCH
from ranking import Criterion, Assessment


@pytest.mark.parametrize('available,requested,shown', [(8, None, 3), (8, 5, 5), (8, 99, 5), (1, None, 1), (2, None, 2)])
def test_agent_order_count_unknowns_and_separate_completion_log(monkeypatch, available, requested, shown):
    rows = [event(i) for i in range(available)]
    message, log, weather = setup(monkeypatch, rows, preferences={
        'Nawar': 'Enjoy music', 'Akash': 'Needs vegetarian food'})
    if requested:
        message.content = 'Show five events'
    args = {'criteria': [Criterion(id='n', person='Nawar', label='Interests', preference='Enjoy music',
                                   source_message='Enjoy music').model_dump(),
                         Criterion(id='a', person='Akash', label='Food', preference='Needs vegetarian food',
                                   source_message='Needs vegetarian food').model_dump()],
            'assessments': [Assessment(event_url=r['url'], criterion_id='n', status='match',
                                      reason='Music matches the stated interest', evidence_field='activities',
                                      evidence_quote='Music').model_dump() for r in rows],
            'ordered_event_urls': [r['url'] for r in reversed(rows)]}
    if requested is not None:
        args['requested_count'] = requested

    async def run(agent, prompt):
        assert 'Enjoy music' in prompt and 'Needs vegetarian food' in prompt
        await invoke(agent, 'search_events', SEARCH)
        result = await invoke(agent, 'rank_events', args)
        assert result['status'] == 'ok'
        assert [r['event']['url'] for r in result['ranked']] == args['ordered_event_urls'][:shown]
        assert all(r['comparisons']['Nawar'][0]['status'] == 'match' for r in result['ranked'])
        assert all(r['comparisons']['Akash'][0]['status'] == 'unknown' for r in result['ranked'])
        message.channel.send.assert_not_called()
        return SimpleNamespace(final_output='Done')

    monkeypatch.setattr(bot.Runner, 'run', run)
    asyncio.run(bot.ask_agent(message))
    message.channel.send.assert_awaited_once()
    call = message.channel.send.call_args
    text = call.args[0] if call.args else '\n'.join(e.description for e in call.kwargs['embeds'])
    assert text.count('🎟️') == shown
    assert '❓ Food:' in text and '✅ Interests:' in text
    if available < 3:
        assert f'{available} verified event(s)' in text
    logs = [c.args[0] for c in log.send.call_args_list]
    assert f'📊 Ranked events | {available} candidates | {shown} shown | Nawar + Akash compared' in logs
    assert 'ranked' not in next(x for x in logs if x.startswith('🔎'))
    assert all('Enjoy music' not in x and 'Needs vegetarian' not in x for x in logs)
    assert bot.search_events.call_args.args[2] == 15


def test_partial_model_never_discards_verified_event(monkeypatch):
    message, log, weather = setup(monkeypatch, [event(1)], preferences={'Nawar': 'Enjoy music'})
    calls = 0
    async def run(agent, prompt):
        nonlocal calls
        calls += 1
        if calls == 1:
            await invoke(agent, 'search_events', SEARCH)
        return SimpleNamespace(final_output='Unusable model answer')
    monkeypatch.setattr(bot.Runner, 'run', run)
    asyncio.run(bot.ask_agent(message))
    text = message.channel.send.call_args.args[0]
    assert 'Event 1' in text and '❓ Preference: Not confirmed from available information' in text
    assert "couldn't complete all" not in text
    assert not any(c.args[0].startswith('📊') for c in log.send.call_args_list)
    weather.assert_awaited_once()
    message.channel.send.assert_awaited_once()


def test_weather_normalizes_before_api_and_deduplicates_relative_iso_and_hours(monkeypatch):
    frozen = datetime(2033, 4, 9, 14, tzinfo=ZoneInfo('America/Toronto'))
    monkeypatch.setattr(event_weather, 'runtime_now', lambda: frozen)
    message, log, weather = setup(monkeypatch, [])
    async def run(agent, prompt):
        for city, day, hour in [('Montréal', 'tomorrow', '19:30'),
                                 ('Montreal, Quebec, Canada', '2033-04-10', '19:00'),
                                 ('Montreal', '2033-04-10', '20:00'),
                                 ('Montreal', '2033-04-11', '19:00')]:
            await invoke(agent, 'get_weather', {'location': city, 'date': day, 'local_time': hour})
        return SimpleNamespace(final_output='Done')
    monkeypatch.setattr(bot.Runner, 'run', run)
    asyncio.run(bot.ask_agent(message))
    assert weather.await_count == 3
    assert weather.call_args_list[0].args == ('Montreal, Quebec, Canada', '2033-04-10', '19:00')
    logs = [c.args[0] for c in log.send.call_args_list if c.args[0].startswith('🔧')]
    assert len(logs) == 3 and all('tomorrow' not in x for x in logs)


def test_exa_candidate_request_is_capped_at_fifteen(monkeypatch):
    monkeypatch.setenv('EXA_API_KEY', 'mock-key')
    transport = AsyncMock(return_value={'results': []})
    monkeypatch.setattr(events.AsyncExa, 'async_request', transport)
    asyncio.run(events.search_events(max_results=100))
    for call in transport.call_args_list:
        payload = call.args[1]
        assert payload['numResults'] <= 15
        assert 'preferences' not in payload
