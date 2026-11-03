"""Bot authorization allowlists."""

from __future__ import annotations

from wth_ai.config import BotConfig


def discord_allowed(
    config: BotConfig,
    *,
    guild_id: int | None = None,
    channel_id: int | None = None,
) -> bool:
    """WU-009 (NET-01): fail-closed. Allowlist kosong berarti belum
    dikonfigurasi operator -- TOLAK semua, bukan terima semua."""
    if not config.discord_guild_ids and not config.discord_channel_ids:
        return False
    if config.discord_guild_ids and guild_id and guild_id not in config.discord_guild_ids:
        return False
    if config.discord_channel_ids and channel_id and channel_id not in config.discord_channel_ids:
        return False
    return True


def telegram_allowed(config: BotConfig, chat_id: int) -> bool:
    """WU-009 (NET-01): fail-closed. Allowlist kosong berarti belum
    dikonfigurasi operator -- TOLAK semua, bukan terima semua."""
    if not config.telegram_chat_ids:
        return False
    return chat_id in config.telegram_chat_ids
