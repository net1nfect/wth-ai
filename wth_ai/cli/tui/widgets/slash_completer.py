"""Slash command input with autocomplete."""

from __future__ import annotations

from prompt_toolkit.completion import Completer, Completion

from wth_ai.platform.registry import get_platform_registry


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        partial = text.split()[-1] if text.split() else text
        for name in get_platform_registry().complete(partial if partial.startswith("/") else text):
            yield Completion(name, start_position=-len(partial) if partial.startswith("/") else 0)
