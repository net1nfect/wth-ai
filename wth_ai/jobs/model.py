"""Job data model and constants."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

JOB_TYPES: tuple[str, ...] = ("recon", "executor", "report", "provider_test", "custom")
JOB_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "stopped",
    "cancelled",
)
JOB_PRIORITIES: tuple[str, ...] = ("low", "normal", "high")
PRIORITY_RANK: dict[str, int] = {"high": 0, "normal": 1, "low": 2}

TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "stopped", "cancelled"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    type: str
    target: str | None = None
    profile: str | None = None
    priority: str = "normal"
    status: str = "queued"
    created_by: str = "cli"
    correlation_id: str | None = None
    job_id: str = field(default_factory=lambda: f"JOB-{uuid.uuid4().hex[:10]}")
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    progress: dict[str, Any] = field(default_factory=lambda: {"current": 0, "total": 100, "message": ""})
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "type": self.type,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "created_by": self.created_by,
            "target": self.target,
            "profile": self.profile,
            "correlation_id": self.correlation_id,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        return cls(
            type=d["type"],
            target=d.get("target"),
            profile=d.get("profile"),
            priority=d.get("priority", "normal"),
            status=d.get("status", "queued"),
            created_by=d.get("created_by", "cli"),
            correlation_id=d.get("correlation_id"),
            job_id=d.get("job_id") or f"JOB-{uuid.uuid4().hex[:10]}",
            created_at=d.get("created_at") or _now(),
            updated_at=d.get("updated_at") or _now(),
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
            progress=d.get("progress") or {"current": 0, "total": 100, "message": ""},
            result=d.get("result") or {},
            error=d.get("error"),
            metadata=d.get("metadata") or {},
        )
