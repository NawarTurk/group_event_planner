"""Tests must never contact live providers, even if a developer has real .env keys."""

import aiohttp
import httpx
import pytest
import requests


@pytest.fixture(autouse=True)
def block_live_provider_calls(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Live provider calls are forbidden in tests")

    async def blocked_async(*args, **kwargs):
        raise AssertionError("Live provider calls are forbidden in tests")

    monkeypatch.setattr(httpx.AsyncClient, "send", blocked_async)
    monkeypatch.setattr(httpx.Client, "send", blocked)
    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", blocked_async)
