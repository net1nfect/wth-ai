"""P3-2: the bot allowlist is the only thing between chat and the shell.

A configured allowlist must DENY an unknown or missing identity. The original
predicates read `if config.guild_ids and guild_id and guild_id not in ...`, so
a DM (no guild id) or a payload without a channel id fell through to `True` --
the allowlist only applied when the caller volunteered the very field being
checked. The handler-level gates (`_channel_allowed`, `_chat_allowed`) had the
same shape.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.bots.auth import discord_allowed, telegram_allowed
from wth_ai.config import BotConfig


# --------------------------------------------------------------------------- #
# the shared predicates
# --------------------------------------------------------------------------- #
def test_telegram_denies_a_chat_outside_the_allowlist():
    cfg = BotConfig(telegram_chat_ids=[111])
    assert telegram_allowed(cfg, 111) is True
    assert telegram_allowed(cfg, 222) is False


def test_telegram_allows_everything_when_no_allowlist_is_set():
    assert telegram_allowed(BotConfig(), 222) is True


def test_discord_denies_a_guild_outside_the_allowlist():
    cfg = BotConfig(discord_guild_ids=[10])
    assert discord_allowed(cfg, guild_id=10, channel_id=1) is True
    assert discord_allowed(cfg, guild_id=99, channel_id=1) is False


def test_discord_denies_a_missing_guild_when_a_guild_allowlist_is_set():
    """A DM has no guild id. Falling through to `True` meant the allowlist
    only applied to traffic that announced a guild -- i.e. never to DMs."""
    cfg = BotConfig(discord_guild_ids=[10])
    assert discord_allowed(cfg, guild_id=None, channel_id=1) is False


def test_discord_denies_a_missing_channel_when_a_channel_allowlist_is_set():
    cfg = BotConfig(discord_channel_ids=[7])
    assert discord_allowed(cfg, guild_id=10, channel_id=None) is False


def test_discord_allows_everything_when_no_allowlist_is_set():
    assert discord_allowed(BotConfig(), guild_id=None, channel_id=None) is True


# --------------------------------------------------------------------------- #
# the gates the handlers actually call
# --------------------------------------------------------------------------- #
def _discord_bot(**bot_kwargs):
    pytest.importorskip("discord")
    from wth_ai.bots.discord_bot import WTHDiscordBot
    from wth_ai.config import WTHConfig

    cfg = WTHConfig()
    cfg.bots = BotConfig(**bot_kwargs)
    return WTHDiscordBot(cfg)


def _telegram_bot(**bot_kwargs):
    pytest.importorskip("telegram")
    from wth_ai.bots.telegram_bot import WTHTelegramBot
    from wth_ai.config import WTHConfig

    cfg = WTHConfig()
    cfg.bots = BotConfig(**bot_kwargs)
    return WTHTelegramBot(cfg)


def test_discord_handler_denies_a_dm_when_a_guild_allowlist_is_set():
    bot = _discord_bot(discord_guild_ids=[10])
    assert bot._channel_allowed(guild_id=None, channel_id=1) is False


def test_discord_handler_denies_an_unknown_channel():
    bot = _discord_bot(discord_channel_ids=[7])
    assert bot._channel_allowed(guild_id=10, channel_id=8) is False
    assert bot._channel_allowed(guild_id=10, channel_id=7) is True


def test_telegram_handler_denies_an_unknown_chat():
    bot = _telegram_bot(telegram_chat_ids=[111])
    assert bot._chat_allowed(222) is False
    assert bot._chat_allowed(111) is True


def test_handlers_stay_open_when_no_allowlist_is_configured():
    assert _discord_bot()._channel_allowed(guild_id=None, channel_id=None) is True
    assert _telegram_bot()._chat_allowed(999) is True