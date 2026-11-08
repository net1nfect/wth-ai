"""Persist terminal transcripts per engagement session."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from wth_ai.models import utc_now
from wth_ai.terminal.models import TerminalResult


class TerminalTranscriptStore:
    """Store and retrieve TX-{id} transcripts."""

    def __init__(self, session_id: str, base_dir: Path | None = None) -> None:
        home = base_dir or Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai")) / "data" / "engagements"
        self.dir = home / session_id / "transcripts"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        self._index: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def _save_index(self) -> None:
        self.index_path.write_text(json.dumps(self._index, ensure_ascii=False, indent=2), encoding="utf-8")

    def store(self, result: TerminalResult) -> str:
        entry = {**result.to_dict(), "timestamp": utc_now()}
        path = self.dir / f"{result.transcript_id}.json"
        path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        self._index.append({"id": result.transcript_id, "actor": result.actor, "command": result.command[:120]})
        self._save_index()
        return result.transcript_id

    def get(self, transcript_id: str) -> dict[str, Any] | None:
        path = self.dir / f"{transcript_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def list_ids(self, limit: int = 50) -> list[str]:
        return [e["id"] for e in self._index[-limit:]]

    def count(self) -> int:
        return len(self._index)
