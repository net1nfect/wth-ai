"""Metadata run: satu file yang menjawab 'apa yang sebenarnya terjadi'."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATUS_COMPLETED = "completed"
STATUS_STOPPED = "stopped"      # budget/turn cap tercapai — BUKAN kegagalan
STATUS_FAILED = "failed"


@dataclass
class RunMeta:
    run_id: str
    targets: list[str] = field(default_factory=list)
    scan_mode: str = "deep"
    status: str = STATUS_COMPLETED
    started_at: str = ""
    finished_at: str = ""
    attempted: int = 0
    confirmed: int = 0
    candidate: int = 0
    unproven: int = 0
    failures: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    coverage: dict = field(default_factory=dict)

    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "RunMeta":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})