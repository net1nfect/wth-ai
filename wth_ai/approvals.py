"""Minimal approval registry (Fase 3).

A lightweight, file-backed store for approval requests. The full Job Manager
arrives in Fase 4; until then jobs are referenced loosely by ``job_id``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from wth_ai.workspace import secure_file, wth_home

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"
STATUS_EXPIRED = "expired"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalRegistry:
    """JSON-backed approval store at ``~/.wth-ai/approvals.json``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (wth_home() / "approvals.json")

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        secure_file(self.path)

    def create(
        self,
        *,
        job_id: str | None,
        action: str,
        risk: str = "medium",
        reason: str = "",
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        approval_id = f"AP-{uuid.uuid4().hex[:10]}"
        created = _now()
        record = {
            "approval_id": approval_id,
            "job_id": job_id,
            "action": action,
            "risk": risk,
            "reason": reason,
            "status": STATUS_PENDING,
            "created_at": created.isoformat(),
            "expires_at": (created + timedelta(seconds=ttl_seconds)).isoformat() if ttl_seconds else None,
        }
        data = self._load()
        data[approval_id] = record
        self._save(data)
        return record

    def get(self, approval_id: str) -> dict[str, Any] | None:
        return self._load().get(approval_id)

    def _expire_due(self, data: dict[str, dict[str, Any]]) -> bool:
        changed = False
        now = _now()
        for rec in data.values():
            if rec["status"] == STATUS_PENDING and rec.get("expires_at"):
                try:
                    exp = datetime.fromisoformat(rec["expires_at"])
                except ValueError:
                    continue
                if exp <= now:
                    rec["status"] = STATUS_EXPIRED
                    changed = True
        return changed

    def list_all(self) -> list[dict[str, Any]]:
        data = self._load()
        if self._expire_due(data):
            self._save(data)
        return sorted(data.values(), key=lambda r: r["created_at"])

    def list_pending(self) -> list[dict[str, Any]]:
        return [r for r in self.list_all() if r["status"] == STATUS_PENDING]

    def _set_status(self, approval_id: str, status: str) -> dict[str, Any] | None:
        data = self._load()
        self._expire_due(data)
        rec = data.get(approval_id)
        if rec is None:
            return None
        if rec["status"] != STATUS_PENDING:
            return rec  # already resolved/expired; return current state
        rec["status"] = status
        rec["resolved_at"] = _now().isoformat()
        self._save(data)
        return rec

    def approve(self, approval_id: str) -> dict[str, Any] | None:
        return self._set_status(approval_id, STATUS_APPROVED)

    def deny(self, approval_id: str) -> dict[str, Any] | None:
        return self._set_status(approval_id, STATUS_DENIED)
