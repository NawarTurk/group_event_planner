"""Application-owned Discord audit messages; never accepts model answer text."""

import logging
import os

import discord

logger = logging.getLogger(__name__)
AUDIT_CHANNEL_NAME = "agent_log"


def _field(value: str) -> str:
    """Keep metadata to one short field, redact configured secrets and disable markup."""
    for name, secret in os.environ.items():
        if secret and name.endswith(("TOKEN", "API_KEY", "SECRET")):
            value = value.replace(secret, "[redacted]")
    value = " ".join(value.replace("|", "/").split())[:100]
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value), as_needed=True) or "unspecified"


async def send_audit_log(guild: discord.Guild | None, message: str) -> discord.Message | None:
    """Best-effort delivery of application-generated operational messages only."""
    try:
        channel = discord.utils.get(guild.text_channels, name=AUDIT_CHANNEL_NAME) if guild else None
        if channel is None:
            logger.warning("Audit log skipped: agent_log channel not found")
            return
        return await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.warning("Audit log skipped: could not write to agent_log", exc_info=True)


async def log_tool_completion(
    guild: discord.Guild | None, tool_name: str, location: str, when: str,
    status: str, source: str | None = None,
) -> None:
    """Log only allowlisted completion metadata, never prompts or full results."""
    icon = {"ok": "✅", "needs_clarification": "⚠️", "unavailable": "⚠️", "failed": "❌"}.get(status, "⚠️")
    message = f"🔧 {_field(tool_name)} | {_field(location)} | {_field(when)} | {icon} {_field(status)}"
    if status == "ok" and source:
        message += f" | {_field(source)}"
    await send_audit_log(guild, message)


async def log_exa_completion(
    guild: discord.Guild | None, location: str, timeframe: str, status: str, count: int = 0,
) -> discord.Message | None:
    """Called only after the event adapter returns, never inferred from model text."""
    if status == "not_configured":
        message = "🔎 Exa search | ❌ not configured"
    else:
        outcome = {"ok": f"✅ {count} candidates", "no_results": "⚠️ no verified results",
                   "needs_clarification": "⚠️ needs_clarification", "failed": "❌ failed"}.get(status, "❌ failed")
        message = f"🔎 Exa search | {_field(location)} | {_field(timeframe)} | {outcome}"
    return await send_audit_log(guild, message)


async def log_exa_ranking(guild: discord.Guild | None, candidates: int, ranked: int) -> None:
    """Record completed application validation of the agent's submitted comparisons."""
    await send_audit_log(guild, f"📊 Ranked events | {candidates} candidates | {ranked} shown | Nawar + Akash compared")


async def log_search_details(guild: discord.Guild | None, result: dict) -> None:
    """Report only metadata returned by actual search executions."""
    calls = result.get("calls", [])
    if not calls:
        return
    await send_audit_log(guild, f"📍 Event search | {_field(result['location'])} | {_field(result['date_or_timeframe'])} | {_field(result['timezone'])}")
    for call in calls:
        icon = "✅" if call["status"] == "ok" else "❌"
        await send_audit_log(guild, f"🔎 Exa call {call['attempt']} | {icon} {call['status']} | {call['returned']} returned")
    await send_audit_log(guild, f"📥 Events | {result['retrieved_count']} retrieved | {result['deduplicated_count']} usable after deduplication")
