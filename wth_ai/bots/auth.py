"""Bot authorization allowlists."""

from __future__ import annotations

from wth_ai.config import BotConfig


def discord_allowed(
    config: BotConfig,
    *,
    guild_id: int | None = None,
    channel_id: int | None = None,
) -> bool:
    # P3-2: an allowlist must fail CLOSED. `guild_id and ...` skipped the check
    # whenever the id was absent, so a DM (no guild) sailed through the very
    # allowlist meant to stop it. Configured + no id == deny.
    if config.discord_guild_ids and guild_id not in config.discord_guild_ids:
        return False
    if config.discord_channel_ids and channel_id not in config.discord_channel_ids:
        return False
    return True


def telegram_allowed(config: BotConfig, chat_id: int) -> bool:
    if config.telegram_chat_ids and chat_id not in config.telegram_chat_ids:
        return False
    return True
