"""LRU session cache for gateway orchestrators."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any

from wth_ai.config import WTHConfig
from wth_ai.orchestrator import WTHOrchestrator


@dataclass
class _Entry:
    orch: WTHOrchestrator
    last_access: float


class SessionCache:
    """Cache WTHOrchestrator per session key with LRU eviction."""

    def __init__(
        self,
        config: WTHConfig,
        *,
        max_sessions: int = 128,
        idle_ttl_seconds: int = 3600,
    ) -> None:
        self.config = config
        self.max_sessions = max_sessions
        self.idle_ttl_seconds = idle_ttl_seconds
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._owners: dict[str, int] = {}
        self._lock = Lock()

    def claim(self, session_key: str, owner_pid: int) -> bool:
        """Klaim kepemilikan session_key oleh satu proses (P2-2).

        Pemanggil pertama yang di-claim menjadi pemilik; klaim dari pid lain
        ditolak sampai pemilik melepas (release). Ini mencegah proses same-user
        lain mem-bypass/spoof session milik client lain.
        """
        with self._lock:
            cur = self._owners.get(session_key)
            if cur is None or cur == owner_pid:
                self._owners[session_key] = owner_pid
                return True
            return False

    def owner_of(self, session_key: str) -> int | None:
        with self._lock:
            return self._owners.get(session_key)

    def release(self, session_key: str) -> None:
        with self._lock:
            self._owners.pop(session_key, None)
            self._entries.pop(session_key, None)

    def get(self, session_key: str, *, fresh: bool = False) -> WTHOrchestrator:
        now = time.time()
        with self._lock:
            self._evict_idle(now)
            if not fresh and session_key in self._entries:
                entry = self._entries[session_key]
                entry.last_access = now
                self._entries.move_to_end(session_key)
                return entry.orch
            orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
            self._entries[session_key] = _Entry(orch=orch, last_access=now)
            self._entries.move_to_end(session_key)
            self._evict_lru()
            return orch

    def remove(self, session_key: str) -> None:
        with self._lock:
            self._entries.pop(session_key, None)

    def list_keys(self) -> list[str]:
        with self._lock:
            return list(self._entries.keys())

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "count": len(self._entries),
                "max": self.max_sessions,
                "sessions": list(self._entries.keys())[:20],
            }

    def _evict_idle(self, now: float) -> None:
        stale = [
            k for k, e in self._entries.items()
            if now - e.last_access > self.idle_ttl_seconds
        ]
        for k in stale:
            del self._entries[k]

    def _evict_lru(self) -> None:
        while len(self._entries) > self.max_sessions:
            self._entries.popitem(last=False)
