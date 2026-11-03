"""Persist cron job definitions."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.workspace import wth_home


@dataclass
class CronEntry:
    cron_id: str
    job_type: str
    target: str
    schedule: str
    profile: str = "recon-only"
    enabled: bool = True
    last_run_at: str | None = None
    next_run_at: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cron_id": self.cron_id,
            "job_type": self.job_type,
            "target": self.target,
            "schedule": self.schedule,
            "profile": self.profile,
            "enabled": self.enabled,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CronEntry":
        return cls(
            cron_id=d["cron_id"],
            job_type=d["job_type"],
            target=d["target"],
            schedule=d["schedule"],
            profile=d.get("profile", "recon-only"),
            enabled=bool(d.get("enabled", True)),
            last_run_at=d.get("last_run_at"),
            next_run_at=d.get("next_run_at"),
            meta=dict(d.get("meta", {})),
        )


class CronStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (wth_home() / "runtime" / "cron.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[CronEntry]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [CronEntry.from_dict(x) for x in raw.get("entries", [])]

    def _save(self, entries: list[CronEntry]) -> None:
        self.path.write_text(
            json.dumps({"entries": [e.to_dict() for e in entries]}, indent=2),
            encoding="utf-8",
        )

    def list(self) -> list[CronEntry]:
        return self._load()

    def add(self, job_type: str, target: str, schedule: str, *, profile: str = "recon-only") -> CronEntry:
        from wth_ai.recon.planner import is_valid_recon_target

        ok, why = is_valid_recon_target(target)
        if not ok:
            # Entri cron MENJALANKAN scan berulang tiap interval; target sampah
            # seperti "--interval" tidak boleh pernah masuk store ini.
            raise ValueError(f"target cron tidak valid: {why}")
        entries = self._load()
        # Idempotent per (job_type, target). `wth recon watch example.com`
        # previously appended a SECOND entry whenever it was re-run (e.g. to
        # change the profile), so the target was scanned twice per interval --
        # double the traffic for one watch. Re-running now updates the existing
        # entry instead of duplicating it.
        for e in entries:
            if e.job_type == job_type and e.target == target:
                e.schedule = schedule
                e.profile = profile
                e.enabled = True
                self._save(entries)
                return e
        entry = CronEntry(
            cron_id=str(uuid.uuid4())[:8],
            job_type=job_type,
            target=target,
            schedule=schedule,
            profile=profile,
        )
        entries.append(entry)
        self._save(entries)
        return entry

    def remove(self, cron_id: str) -> bool:
        entries = self._load()
        new = [e for e in entries if e.cron_id != cron_id]
        if len(new) == len(entries):
            return False
        self._save(new)
        return True

    def set_enabled(self, cron_id: str, enabled: bool) -> bool:
        entries = self._load()
        found = False
        for e in entries:
            if e.cron_id == cron_id:
                e.enabled = enabled
                found = True
        if found:
            self._save(entries)
        return found

    def disable_with_reason(self, cron_id: str, reason: str) -> bool:
        """Matikan entri + simpan alasannya di `meta`.

        Dipakai scheduler saat menemukan entri LAMA dengan target tidak valid
        (dibuat sebelum guard ada): daripada menjalankan scan sampah tiap
        interval, entri dimatikan dan alasannya terlihat lewat `wth cron list`.
        """
        from datetime import datetime, timezone

        entries = self._load()
        found = False
        for e in entries:
            if e.cron_id == cron_id:
                e.enabled = False
                e.meta["disabled_reason"] = reason
                e.meta["disabled_at"] = datetime.now(timezone.utc).isoformat()
                found = True
        if found:
            self._save(entries)
        return found

    def compute_next_run(self, entry: CronEntry) -> str | None:
        from wth_ai.cron.scheduler import _parse_schedule
        from datetime import datetime, timedelta, timezone

        interval = _parse_schedule(entry.schedule)
        if interval is None:
            return None
        base = datetime.now(timezone.utc)
        if entry.last_run_at:
            try:
                base = datetime.fromisoformat(entry.last_run_at)
            except ValueError:
                pass
        return (base + timedelta(seconds=interval)).isoformat()

    def refresh_next_runs(self) -> None:
        entries = self._load()
        for e in entries:
            e.next_run_at = self.compute_next_run(e)
        self._save(entries)

    def migrate_from_watch(self) -> int:
        """Import legacy recon watch configs as cron entries."""
        from wth_ai.recon.realtime import list_watches
        migrated = 0
        existing = {(e.target, e.schedule) for e in self._load()}
        for wc in list_watches():
            sched = f"every {max(wc.interval_seconds // 3600, 1)}h"
            if (wc.target, sched) in existing:
                continue
            self.add("recon", wc.target, sched, profile=wc.profile)
            migrated += 1
        return migrated

    def update_run(self, cron_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        entries = self._load()
        for e in entries:
            if e.cron_id == cron_id:
                e.last_run_at = now
        self._save(entries)
