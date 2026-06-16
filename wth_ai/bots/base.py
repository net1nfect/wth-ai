"""Bot session manager — per-user orchestrator instances."""

from __future__ import annotations

from threading import Lock

from wth_ai.config import WTHConfig, WTH_HOME
from wth_ai.orchestrator import WTHOrchestrator


class BotSessionManager:
    """Maps platform user IDs to isolated WTHOrchestrator sessions."""

    def __init__(self, config: WTHConfig) -> None:
        self.config = config
        self._sessions: dict[str, WTHOrchestrator] = {}
        self._lock = Lock()
        self.base_dir = WTH_HOME / "data" / "bot_sessions"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, platform: str, user_id: str | int) -> str:
        return f"{platform}_{user_id}"

    def get_or_create(self, platform: str, user_id: str | int) -> WTHOrchestrator:
        key = self._key(platform, user_id)
        with self._lock:
            if key not in self._sessions:
                session_dir = self.base_dir / key
                session_dir.mkdir(parents=True, exist_ok=True)
                orch = WTHOrchestrator(self.config, fresh=True)
                self._sessions[key] = orch
            return self._sessions[key]

    def run_command(self, platform: str, user_id: str | int, text: str) -> str:
        orch = self.get_or_create(platform, user_id)
        return orch.run_once(text)

    def session_count(self) -> int:
        return len(self._sessions)

    def clear(self, platform: str, user_id: str | int) -> None:
        key = self._key(platform, user_id)
        with self._lock:
            self._sessions.pop(key, None)
