"""Re-export command classification for TUI (canonical: wth_ai.cli.command_class)."""

from wth_ai.cli.command_class import (  # noqa: F401
    classify_command,
    is_quick_command,
    should_reset_activity_feed,
)
