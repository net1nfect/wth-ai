"""Slash-command completion.

Was: names only, so the menu listed `/recon` `/report` `/resume` and left the
operator to guess which one does what. Now every entry carries the command's own
description and category icon, and commands whose first argument is a known set
(e.g. `/tools show <name>`) complete that argument too.
"""

from __future__ import annotations

from typing import Iterable

from prompt_toolkit.completion import Completer, Completion

from wth_ai.platform.registry import get_platform_registry


def _tool_names() -> list[str]:
    """Installed+known recon tool names, for `/tools show <name>`."""
    try:
        from wth_ai.recon.registry import ReconToolRegistry

        return sorted(ReconToolRegistry().tools.keys())
    except Exception:  # noqa: BLE001 - completion must never break the prompt
        return []


#: command -> provider of its first-argument candidates
_ARG_COMPLETERS: dict[str, str] = {
    "/tools": "tools",
    "/recon": "tools",
    "/recon-tools": "tools",
}

#: Subcommands that take a NAME as their next word, so the name can be completed.
_NAME_TAKING_SUBCOMMANDS = frozenset({"show", "enable", "disable", "info", "run", "plan"})


class SlashCompleter(Completer):
    """Completes slash commands with descriptions and known first arguments."""

    def __init__(self, *, include_args: bool = True) -> None:
        self.include_args = include_args

    def get_completions(self, document, complete_event) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        stripped = text.strip()

        # Argument position. Splitting on WORDS (not "first space") is the fix:
        # an earlier version partitioned once and then bailed whenever the
        # remainder still contained a space, so `/tools show sub<TAB>` completed
        # nothing at all.
        if self.include_args and " " in stripped:
            parts = stripped.split()
            if len(parts) < 2:
                return
            kind = _ARG_COMPLETERS.get(parts[0])
            if kind is None:
                return
            trailing_space = stripped.endswith(" ")

            # "/tools sh" -> complete the subcommand itself
            if len(parts) == 2 and not trailing_space:
                for sub in sorted(_NAME_TAKING_SUBCOMMANDS):
                    if sub.startswith(parts[1]):
                        yield Completion(sub, start_position=-len(parts[1]),
                                         display=sub, display_meta="subcommand")
                return

            # "/tools show <partial>" -> complete a tool name
            subcommand = parts[1]
            if subcommand not in _NAME_TAKING_SUBCOMMANDS:
                return
            partial = "" if trailing_space or len(parts) == 2 else parts[-1]
            for name in self._arg_candidates(kind, partial):
                yield Completion(name, start_position=-len(partial),
                                 display=name, display_meta="tool")
            return

        from wth_ai.cli.ui.theme import category_icon

        partial = stripped
        for cmd in self._commands(partial):
            icon, colour = category_icon(getattr(cmd, "category", ""))
            yield Completion(
                cmd.name,
                start_position=-len(partial),
                display=cmd.name,
                display_meta=f"{icon} {cmd.description}",
                style=colour,
            )

    @staticmethod
    def _commands(partial: str):
        registry = get_platform_registry()
        commands = list(getattr(registry, "commands", []))
        by_name = {c.name: c for c in commands}
        names = registry.complete(partial)

        # Subsequence fallback: `/rcn` should still find `/recon`. Cheap, and it
        # is what "fuzzy" meant here in the first place.
        if not names and partial:
            # Jalur saran yang SAMA dengan pesan unknown-command (`/rcon` ->
            # `/recon`), supaya menu completion dan pesan error menawarkan hal
            # yang konsisten.
            names = registry.suggestions(partial, limit=8)

        # Keep registry order, drop anything that cannot be described: a menu
        # entry with no description is the thing this module exists to remove.
        return [by_name[n] for n in names if n in by_name] or []

    @staticmethod
    def _arg_candidates(kind: str, partial: str) -> list[str]:
        if kind != "tools":
            return []
        names = _tool_names()
        partial = partial.lower()
        exact = [n for n in names if n.lower().startswith(partial)]
        fuzzy = [n for n in names if partial and partial in n.lower() and n not in exact]
        return (exact + fuzzy)[:50]


def _subsequence(needle: str, haystack: str) -> bool:
    """True when `needle` appears in `haystack` in order (not necessarily adjacent)."""
    if not needle:
        return False
    it = iter(haystack)
    return all(ch in it for ch in needle)


def build_completer() -> Completer:
    """Completer wired for the REPL.

    NOT wrapped in `FuzzyCompleter`, and that is a measured decision, not a
    preference: `FuzzyCompleter` strips the word before the cursor before calling
    the inner completer, so `SlashCompleter` received an empty string, its
    `text.startswith("/")` guard failed, and typing `/rec<TAB>` produced an EMPTY
    menu in the real REPL. Fuzzy matching lives inside `_commands()` instead,
    where the slash prefix is still visible.
    """
    return SlashCompleter()
