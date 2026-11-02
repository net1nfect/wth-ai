"""MEMORY.md / USER.md operator context files."""

from __future__ import annotations

from pathlib import Path

from wth_ai.workspace import wth_home

MAX_CHARS = 8000


def memory_path() -> Path:
    return wth_home() / "MEMORY.md"


def user_path() -> Path:
    return wth_home() / "USER.md"


def read_memory() -> str:
    p = memory_path()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")[:MAX_CHARS]


def read_user() -> str:
    p = user_path()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")[:MAX_CHARS]


def write_memory(text: str) -> None:
    memory_path().write_text(text[:MAX_CHARS], encoding="utf-8")


def write_user(text: str) -> None:
    user_path().write_text(text[:MAX_CHARS], encoding="utf-8")


def context_block() -> str:
    mem = read_memory().strip()
    usr = read_user().strip()
    parts: list[str] = []
    if usr:
        parts.append(f"## Operator preferences\n{usr}")
    if mem:
        parts.append(f"## Engagement memory\n{mem}")
    return "\n\n".join(parts)
