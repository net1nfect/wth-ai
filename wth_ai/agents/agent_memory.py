"""Persistent agent memory: lessons + notes + bookmarks for self-improvement.

(a la Strix "Notes", with a longer self-improvement flavor). The agent writes
`self_improve` lessons after a meaningful result, and `memory_write` keeps
campaign facts (credentials found, interesting endpoints, gotchas) across
sessions — the point is to work even if the target is different.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


def _memory_path() -> Path:
    try:
        from wth_ai.workspace import wth_home

        base = wth_home() / "memory"
    except Exception:  # noqa: BLE001
        base = Path.home() / ".wth-ai" / "memory"
    base.mkdir(parents=True, exist_ok=True)
    return base / "agent_memory.json"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load(path: Path | None = None) -> dict[str, Any]:
    p = path if path is not None else _memory_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"lessons": [], "notes": {}, "bookmarks": []}


def write_lesson(topic: str, lesson: str, *, evidence: str = "", path: Path | None = None) -> dict[str, Any]:
    data = _load(path)
    entry = {
        "id": uuid4().hex[:10],
        "topic": topic.strip() or "general",
        "lesson": lesson.strip(),
        "evidence": evidence.strip(),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data.setdefault("lessons", []).append(entry)
    data["lessons"] = data["lessons"][-100:]
    _atomic_write(p if path is not None else _memory_path(), data)
    return entry


def read_lessons(topic: str = "", *, limit: int = 10, path: Path | None = None) -> list[dict[str, Any]]:
    lessons = _load(path).get("lessons") or []
    if topic.strip():
        needle = topic.strip().lower()
        lessons = [l for l in lessons if needle in str(l.get("topic", "")).lower()]
    return lessons[-limit:]


def write_note(key: str, value: str, *, path: Path | None = None) -> str:
    data = _load(path)
    data.setdefault("notes", {})[key.strip() or "note"] = value.strip()[:4000]
    data["notes"] = dict(list(data["notes"].items())[-50:])
    _atomic_write(_memory_path(), data)
    return key.strip() or "note"


def read_note(key: str = "", *, path: Path | None = None) -> dict[str, Any]:
    data = _load(path)
    notes = data.get("notes") or {}
    if key.strip():
        return {"key": key.strip(), "value": notes.get(key.strip())}
    return {"notes": notes, "count": len(notes)}


def recent_lessons_context(*, limit: int = 5, path: Path | None = None) -> str:
    """Rangkuman pelajaran terbaru untuk context block supervisor."""
    lessons = read_lessons(limit=limit, path=path)
    if not lessons:
        return ""
    lines = ["## Lessons learned from the last session"]
    for l in lessons:
        ev = f" [{l['evidence'][:60]}]" if l.get("evidence") else ""
        lines.append(f"- ({l['topic']}) {l['lesson'][:200]}{ev}")
    return "\n".join(lines)


def snapshot(path_dir: Path | None = None) -> dict[str, Any]:
    data = _load(None)
    return {
        "lessons": len(data.get("lessons") or []),
        "notes": len(data.get("notes") or {}),
        "recent": read_lessons(limit=3),
    }
