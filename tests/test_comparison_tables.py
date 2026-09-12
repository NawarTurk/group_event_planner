import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot
from ranking import Criterion, Assessment, rank_candidates, render_scorecards
from test_required_event_weather import event, invoke, setup, SEARCH


def test_table_has_all_rows_and_independent_evidence_backed_cells():
    row = event(1)
    row['food'] = 'Chicken available'
    criteria = [Criterion(id='n', person='Nawar', label='Food', category='Food',
                          preference='Likes chicken', source_message='Likes chicken'),
                Criterion(id='a', person='Akash', label='Food', category='Food',
                          preference='Vegetarian', source_message='Vegetarian')]
    checks = [Assessment(event_url=row['url'], criterion_id='n', status='match',
                         reason='Chicken available', evidence_field='food', evidence_quote='Chicken available')]
    result = rank_candidates([row], {'Nawar': ['Likes chicken'], 'Akash': ['Vegetarian']}, criteria, checks)
    text = render_scorecards(result)
    assert '| Criterion | Nawar | Akash |' in text
    for category in ('Food', 'Price', 'Day/time', 'Vibe/interests', 'Setting', 'Weather'):
        assert f'| {category} |' in text
    assert '| Food | ✅ Food: Chicken available | ❓ Food: Not confirmed from available information |' in text
    assert '| Price | ❓ Not confirmed | ❓ Not confirmed |' in text
    assert row['url'] in text
    assert len(result) == 1  # One green check suffices; no compatibility filter.


@pytest.mark.parametrize('count', [1, 3, 5])
def test_logs_follow_real_boundaries_and_imperfect_events_are_displayed(monkeypatch, count):
    rows = [event(i) for i in range(6)]
    message, log, weather = setup(monkeypatch, rows)
    async def search(*args):
        assert log.send.call_args.args[0] == '🔎 Exa search started'
        return {'status': 'ok', 'events': rows, 'location': 'Montreal', 'date_or_timeframe': 'runtime range'}
    monkeypatch.setattr(bot, 'search_events', search)
    async def run(agent, prompt):
        returned = await invoke(agent, 'search_events', SEARCH)
        assert len(returned['events']) == 6
        assert log.send.call_args.args[0] == '📤 Candidates passed to agent | 6'
        await invoke(agent, 'rank_events', {'criteria': [], 'assessments': [], 'requested_count': count,
                                           'ordered_event_urls': [e['url'] for e in reversed(rows)]})
        return SimpleNamespace(final_output='No events suit both of you')
    async def send(*args, **kwargs):
        assert not any(c.args[0].startswith(('📨 Events displayed', '📊 Ranked events')) for c in log.send.call_args_list)
    message.channel.send.side_effect = send
    monkeypatch.setattr(bot.Runner, 'run', run)
    asyncio.run(bot.ask_agent(message))
    call = message.channel.send.call_args
    text = call.args[0] if call.args else '\n'.join(e.description for e in call.kwargs['embeds'])
    assert 'No events suit' not in text
    assert text.count('| Criterion | Nawar | Akash |') == count
    assert text.count('🎟️') == count
    assert log.send.call_args.args[0] == f'📨 Events displayed | {count}'
    message.channel.send.assert_awaited_once()
    weather.assert_awaited_once()


def test_failed_discord_send_cannot_claim_events_displayed(monkeypatch):
    message, log, _ = setup(monkeypatch, [event(1)])
    async def run(agent, prompt):
        await invoke(agent, 'search_events', SEARCH)
        await invoke(agent, 'rank_events', {'criteria': [], 'assessments': []})
        return SimpleNamespace(final_output='Done')
    monkeypatch.setattr(bot.Runner, 'run', run)
    message.channel.send.side_effect = RuntimeError('Discord rejected delivery')
    with pytest.raises(RuntimeError, match='Discord rejected'):
        asyncio.run(bot.ask_agent(message))
    assert not any(c.args[0].startswith(('📨 Events displayed', '📊 Ranked events')) for c in log.send.call_args_list)
