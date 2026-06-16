"""WTH-AI bot integrations."""

from wth_ai.bots.base import BotSessionManager

__all__ = ["BotSessionManager", "run_discord_bot", "run_telegram_bot"]


def run_discord_bot(config=None):
    from wth_ai.bots.discord_bot import run_discord_bot as _run
    return _run(config)


def run_telegram_bot(config=None):
    from wth_ai.bots.telegram_bot import run_telegram_bot as _run
    return _run(config)
