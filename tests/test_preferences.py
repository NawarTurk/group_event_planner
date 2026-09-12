import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import bot


def preference_channel(name: str, count: int = 0, fail: bool = False):
    async def history(limit):
        for i in range(count):
            yield SimpleNamespace(content=f"private-{name}-{i}",
                                  author=SimpleNamespace(bot=False, display_name="private-name"))
        if fail:
            raise RuntimeError("private API error")
        # Neither bot posts nor textless messages are loaded into the prompt.
        yield SimpleNamespace(content="private-bot-text", author=SimpleNamespace(bot=True))
        yield SimpleNamespace(content="   ", author=SimpleNamespace(bot=False))

    return SimpleNamespace(name=name, history=Mock(side_effect=history))


def setup(monkeypatch, preference_channels):
    general = SimpleNamespace(name="general", send=AsyncMock())
    log = SimpleNamespace(name="agent_log", send=AsyncMock())
    guild = SimpleNamespace(text_channels=[general, log, *preference_channels])
    message = SimpleNamespace(content="hello", guild=guild, channel=general,
                              author=SimpleNamespace(bot=False))
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "test-key")
    return message, log


def test_both_histories_counted_privately_and_added_before_runner(monkeypatch):
    nawar = preference_channel("nawar-preferences", 5)
    akash = preference_channel("akash-preferences", 4)
    message, log = setup(monkeypatch, [nawar, akash])

    async def run(agent, prompt):
        nawar.history.assert_called_once_with(limit=None)
        akash.history.assert_called_once_with(limit=None)
        log.send.assert_awaited_once()
        assert log.send.call_args.args[0] == (
            "📚 Preferences loaded | nawar-preferences: ✅ 5 messages | akash-preferences: ✅ 4 messages"
        )
        assert "private" not in log.send.call_args.args[0]
        for name, count in (("nawar-preferences", 5), ("akash-preferences", 4)):
            for i in range(count):
                assert f"private-{name}-{i}" in prompt
        assert "private-bot-text" not in prompt
        assert "User request: hello" in prompt
        return SimpleNamespace(final_output="Hello!")

    runner = AsyncMock(side_effect=run)
    monkeypatch.setattr(bot.Runner, "run", runner)
    asyncio.run(bot.on_message(message))
    runner.assert_awaited_once()
    log.send.assert_awaited_once()
    message.channel.send.assert_awaited_once_with("Hello!")


@pytest.mark.parametrize("broken_name", ["nawar-preferences", "akash-preferences"])
@pytest.mark.parametrize("state", ["missing", "empty", "access failed"])
def test_unavailable_channel_does_not_block_other_context(monkeypatch, broken_name, state):
    other_name = next(name for name in bot.PREFERENCE_CHANNELS if name != broken_name)
    other = preference_channel(other_name, 2)
    broken = preference_channel(broken_name, count=1 if state == "access failed" else 0,
                                fail=state == "access failed")
    message, log = setup(monkeypatch, [other] + ([] if state == "missing" else [broken]))

    async def run(agent, prompt):
        other.history.assert_called_once_with(limit=None)
        if state != "missing":
            broken.history.assert_called_once_with(limit=None)
        assert f"private-{other_name}-0" in prompt
        assert f"private-{broken_name}" not in prompt  # discard incomplete reads
        icon = {"missing": "❌", "empty": "⚪", "access failed": "⚠️"}[state]
        record = log.send.call_args.args[0]
        assert record.startswith("📚 Preferences loaded")
        assert f"{broken_name}: {icon} {state}" in record
        assert f"{other_name}: ✅ 2 messages" in record
        assert "private" not in record
        return SimpleNamespace(final_output="Plan")

    monkeypatch.setattr(bot.Runner, "run", AsyncMock(side_effect=run))
    asyncio.run(bot.ask_agent(message))
    log.send.assert_awaited_once()
    message.channel.send.assert_awaited_once_with("Plan")


def test_no_context_is_checked_not_loaded(monkeypatch):
    nawar = preference_channel("nawar-preferences")
    akash = preference_channel("akash-preferences")
    message, log = setup(monkeypatch, [nawar, akash])
    runner = AsyncMock(return_value=SimpleNamespace(final_output="Hello!"))
    monkeypatch.setattr(bot.Runner, "run", runner)
    asyncio.run(bot.ask_agent(message))
    assert runner.call_args.args[1] == "hello"
    assert log.send.call_args.args[0] == (
        "📚 Preferences checked | nawar-preferences: ⚪ empty | akash-preferences: ⚪ empty"
    )
    log.send.assert_awaited_once()


def test_preference_audit_send_failure_preserves_prompt_and_reply(monkeypatch, caplog):
    message, log = setup(monkeypatch, [preference_channel(name, 1) for name in bot.PREFERENCE_CHANNELS])
    log.send.side_effect = RuntimeError("permission denied")
    runner = AsyncMock(return_value=SimpleNamespace(final_output="Plan"))
    monkeypatch.setattr(bot.Runner, "run", runner)
    asyncio.run(bot.ask_agent(message))
    for name in bot.PREFERENCE_CHANNELS:
        assert f"private-{name}-0" in runner.call_args.args[1]
    log.send.assert_awaited_once()
    message.channel.send.assert_awaited_once_with("Plan")
    assert "could not write to agent_log" in caplog.text
