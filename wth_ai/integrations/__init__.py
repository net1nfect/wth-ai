"""Integration layer: event bus consumers that route notifications to adapters.

Core never imports adapters directly — it publishes events to the EventBus and
the IntegrationManager fans them out to enabled adapters.
"""

from wth_ai.integrations.base import NotificationAdapter, command_allowed, resolve_discord_role
from wth_ai.integrations.commands import BotCommandService
from wth_ai.integrations.discord import DiscordNotifier
from wth_ai.integrations.manager import IntegrationManager
from wth_ai.integrations.telegram import TelegramNotifier
from wth_ai.integrations.webhook import WebhookNotifier

__all__ = [
    "NotificationAdapter",
    "command_allowed",
    "resolve_discord_role",
    "BotCommandService",
    "DiscordNotifier",
    "IntegrationManager",
    "TelegramNotifier",
    "WebhookNotifier",
]
