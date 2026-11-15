"""Per-target adaptive memory: which mutations actually worked.

Persisted as JSON under `$WTH_HOME/adaptive/<host>.json`. Writes are atomic
(tmp + replace) and guarded by a per-process lock. This is measurement, not
guesswork: every attempt updates tries/wins/blocks for the strategy used.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _safe_host(host: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", (host or "target").strip())[:120]
    return value or "target"


def default_memory_dir() -> Path:
    try:
        from wth_ai.workspace import wth_home

        return wth_home() / "adaptive"
    except Exception:  # noqa: BLE001
        return Path.home() / ".wth-ai" / "adaptive"


@dataclass
class StrategyStat:
    tries: int = 0
    wins: int = 0
    blocks: int = 0
    last_status: int = 0
    last_signal: str = ""
    updated: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tries": self.tries, "wins": self.wins, "blocks": self.blocks,
            "last_status": self.last_status, "last_signal": self.last_signal,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyStat":
        return cls(
            tries=int(data.get("tries", 0) or 0),
            wins=int(data.get("wins", 0) or 0),
            blocks=int(data.get("blocks", 0) or 0),
            last_status=int(data.get("last_status", 0) or 0),
            last_signal=str(data.get("last_signal", "") or ""),
            updated=float(data.get("updated", 0) or 0),
        )


@dataclass
class TargetMemory:
    host: str
    path: Path
    attempts: int = 0
    signals: dict[str, int] = field(default_factory=dict)
    strategies: dict[str, StrategyStat] = field(default_factory=dict)
    best_strategy: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    @classmethod
    def load(cls, host: str, directory: Path | None = None) -> "TargetMemory":
        base = Path(directory) if directory is not None else default_memory_dir()
        path = base / f"{_safe_host(host)}.json"
        with _LOCK:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        memory = cls(host=host, path=path)
        memory.attempts = int(data.get("attempts", 0) or 0)
        memory.signals = {str(k): int(v) for k, v in (data.get("signals") or {}).items()}
        memory.strategies = {
            str(k): StrategyStat.from_dict(v)
            for k, v in (data.get("strategies") or {}).items() if isinstance(v, dict)
        }
        memory.best_strategy = str(data.get("best_strategy", "") or "")
        memory.findings = list(data.get("findings") or [])
        memory.created = float(data.get("created", memory.created) or memory.created)
        memory.updated = float(data.get("updated", memory.updated) or memory.updated)
        return memory

    def score(self, strategy: str) -> float:
        """Laplace-smoothed success rate with a small exploration bonus."""
        stat = self.strategies.get(strategy)
        if stat is None:
            return 1.0 + 0.1  # unknown strategy: slightly above neutral, explore it
        rate = (stat.wins + 1.0) / (stat.tries + 2.0)
        explore = 1.0 + 0.15 * math.log1p(stat.tries)
        return round(rate * explore, 4)

    def record(self, strategy: str, *, signal: str, status: int, improved: bool, evidence: str = "") -> None:
        stat = self.strategies.setdefault(strategy, StrategyStat())
        stat.tries += 1
        stat.last_status = int(status or 0)
        stat.last_signal = signal
        stat.updated = time.time()
        if improved:
            stat.wins += 1
            if self.strategies[strategy].wins >= self.strategies.get(self.best_strategy, StrategyStat()).wins:
                self.best_strategy = strategy
        if signal in {"blocked", "waf", "rate_limited", "auth_required"}:
            stat.blocks += 1
        self.attempts += 1
        self.signals[signal] = self.signals.get(signal, 0) + 1
        self.updated = time.time()
        self.save()

    def add_finding(self, finding: dict[str, Any]) -> None:
        self.findings.append(finding)
        self.findings = self.findings[-200:]
        self.updated = time.time()
        self.save()

    def top(self, limit: int = 8) -> list[dict[str, Any]]:
        rows = []
        for name, stat in self.strategies.items():
            rows.append({
                "strategy": name, "tries": stat.tries, "wins": stat.wins,
                "blocks": stat.blocks, "rate": round((stat.wins / stat.tries) if stat.tries else 0.0, 3),
                "score": self.score(name), "last_status": stat.last_status,
                "last_signal": stat.last_signal,
            })
        rows.sort(key=lambda r: (-r["score"], -r["wins"], r["strategy"]))
        return rows[:limit]

    def save(self) -> None:
        payload = {
            "host": self.host,
            "attempts": self.attempts,
            "signals": self.signals,
            "strategies": {k: v.to_dict() for k, v in self.strategies.items()},
            "best_strategy": self.best_strategy,
            "findings": self.findings,
            "created": self.created,
            "updated": self.updated,
        }
        with _LOCK:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError:
                pass

    def snapshot(self) -> dict[str, Any]:
        return {
            "host": self.host, "path": str(self.path), "attempts": self.attempts,
            "signals": dict(self.signals), "best_strategy": self.best_strategy,
            "top": self.top(), "findings": len(self.findings),
        }
