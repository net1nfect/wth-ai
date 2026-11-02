"""Realtime / watch configuration for periodic recon.

This module does NOT spam requests. It stores a watch config (target, interval,
jitter, last job) and computes the next eligible run time with jitter. Actual
scheduling is driven by the job system; ``watch`` simply persists intent and
records each run so :mod:`wth_ai.recon.diff` can compare against the prior run.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from wth_ai.workspace import wth_home

MIN_INTERVAL_SECONDS = 3600  # 1 hour hard floor — no aggressive polling
JITTER_RATIO = 0.1


def _watch_dir() -> Path:
    return wth_home() / "recon" / "watch"


def _slug(target: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", target)
    return f"{safe}-{hashlib.sha1(target.encode()).hexdigest()[:8]}"


def parse_interval(text: str) -> int:
    """Parse '6h', '90m', '1d', '3600' -> seconds (floored at 1h)."""
    text = (text or "").strip().lower()
    m = re.fullmatch(r"(\d+)([smhd]?)", text)
    if not m:
        return MIN_INTERVAL_SECONDS
    value = int(m.group(1))
    unit = m.group(2) or "s"
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return max(value * mult, MIN_INTERVAL_SECONDS)


@dataclass
class WatchConfig:
    target: str
    interval_seconds: int = MIN_INTERVAL_SECONDS
    profile: str = "recon-only"
    last_run_at: str | None = None
    last_job_id: str | None = None
    runs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "interval_seconds": self.interval_seconds,
            "profile": self.profile,
            "last_run_at": self.last_run_at,
            "last_job_id": self.last_job_id,
            "runs": self.runs,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WatchConfig":
        return cls(
            target=d["target"],
            interval_seconds=int(d.get("interval_seconds", MIN_INTERVAL_SECONDS)),
            profile=d.get("profile", "recon-only"),
            last_run_at=d.get("last_run_at"),
            last_job_id=d.get("last_job_id"),
            runs=list(d.get("runs", [])),
        )

    def next_run_at(self, *, now: datetime | None = None) -> datetime:
        now = now or datetime.now(timezone.utc)
        base = self.interval_seconds
        jitter = random.uniform(-JITTER_RATIO, JITTER_RATIO) * base
        return now + timedelta(seconds=base + jitter)


def save_watch(cfg: WatchConfig) -> Path:
    d = _watch_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_slug(cfg.target)}.json"
    path.write_text(json.dumps(cfg.to_dict(), indent=2), encoding="utf-8")
    return path


def load_watch(target: str) -> WatchConfig | None:
    path = _watch_dir() / f"{_slug(target)}.json"
    if not path.exists():
        return None
    try:
        return WatchConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def list_watches() -> list[WatchConfig]:
    d = _watch_dir()
    if not d.exists():
        return []
    out: list[WatchConfig] = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(WatchConfig.from_dict(json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return out


def record_run(target: str, job_id: str, *, interval_seconds: int | None = None, profile: str | None = None) -> WatchConfig:
    cfg = load_watch(target) or WatchConfig(target=target)
    if interval_seconds is not None:
        cfg.interval_seconds = max(interval_seconds, MIN_INTERVAL_SECONDS)
    if profile is not None:
        cfg.profile = profile
    cfg.last_run_at = datetime.now(timezone.utc).isoformat()
    cfg.last_job_id = job_id
    cfg.runs.append(job_id)
    cfg.runs = cfg.runs[-25:]
    save_watch(cfg)
    return cfg
