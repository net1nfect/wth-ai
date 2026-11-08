"""Persistent session state for resume after context/token limits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wth_ai.config import WTHConfig
from wth_ai.models import SessionState
from wth_ai.workspace import atomic_write_text, wth_home


class SessionStore:
    """JSON file-backed session persistence."""

    def __init__(self, config: WTHConfig) -> None:
        # P1-3: `state_dir` default relatif, jadi path relatif dulu ditulis ke
        # direktori kerja dan dua WTH_HOME bisa berbagi `latest.json`.
        self.dir = Path(config.session.state_dir)
        if not self.dir.is_absolute():
            self.dir = wth_home() / self.dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.auto_save = config.session.auto_save

    def _path(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.json"

    def save(self, state: SessionState) -> Path:
        state.touch()
        path = self._path(state.session_id)
        # P1-4: tmp+fsync+rename -- sesi lama tidak boleh hilang karena tulis
        # yang terpotong.
        atomic_write_text(path, json.dumps(state.to_dict(), indent=2, ensure_ascii=False))
        atomic_write_text(self.dir / "latest.json",
                          json.dumps({"session_id": state.session_id}, indent=2))
        return path

    def load(self, session_id: str) -> SessionState | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return SessionState.from_dict(data)

    def load_latest(self) -> SessionState | None:
        latest = self.dir / "latest.json"
        if not latest.exists():
            return None
        meta = json.loads(latest.read_text(encoding="utf-8"))
        return self.load(meta.get("session_id", ""))

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = []
        for path in sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if path.name == "latest.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions.append({
                    "session_id": data.get("session_id"),
                    "target": data.get("scope", {}).get("target"),
                    "phase": data.get("current_phase"),
                    "updated_at": data.get("updated_at"),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return sessions

    def resume_context_summary(self, state: SessionState) -> str:
        """Compact summary injected when resuming after token/context reset."""
        lines = [
            "=== RESUME CONTEXT ===",
            f"Session: {state.session_id}",
            f"Target: {state.scope.target}",
            f"Permission: {state.permission_mode.value}",
            f"Phase: {state.current_phase.value}",
            f"Assets: {', '.join(state.known_assets[:20]) or 'none'}",
            f"Attack surface: {', '.join(state.attack_surface[:15]) or 'none'}",
            f"Open hypotheses: {len(state.hypotheses)}",
            f"Findings: {len(state.findings)}",
            f"Pending tasks: {len(state.task_queue)}",
            f"Completed tasks: {len(state.completed_tasks)}",
        ]
        if state.planner_summary:
            lines.append(f"Planner summary: {state.planner_summary[:2000]}")
        if state.hypotheses:
            lines.append("Top hypotheses:")
            for h in state.hypotheses[:5]:
                lines.append(f"  - [{h.get('id')}] {h.get('title')} ({h.get('state', 'open')})")
        if state.findings:
            lines.append("Findings snapshot:")
            for f in state.findings[:5]:
                lines.append(f"  - [{f.get('id')}] {f.get('title')} — {f.get('state')} ({f.get('severity')})")
        if state.task_queue:
            lines.append("Next tasks:")
            for t in state.task_queue[:3]:
                lines.append(f"  - [{t.get('id')}] {t.get('goal')}")
        lines.append("=== END RESUME ===")
        return "\n".join(lines)
