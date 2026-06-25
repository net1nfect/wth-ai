"""Continuous operation loop (Fase 6, flowchart Stage 10).

Provides change detection between recon runs, delta reports, and a retest queue
builder. Feedback edges (new asset -> recon, risk refresh -> prioritization,
retest -> planning) are realized by creating follow-up jobs through the existing
JobManager — never by bypassing the job system.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wth_ai.recon.diff import diff_results


def _load_normalized(store: Any, job_id: str) -> dict[str, Any] | None:
    if not store or not job_id:
        return None
    path = Path(store.results_dir) / job_id / "normalized_result.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def delta_report(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compute a structured delta + a short markdown summary between two runs."""
    changes = diff_results(old or {}, new or {})
    lines = ["# Recon Delta Report", ""]
    if not changes.get("changed"):
        lines.append("No changes detected since the previous run.")
    else:
        for key, val in changes.items():
            if isinstance(val, list) and val:
                lines.append(f"- **{key}**: {len(val)}")
                for item in val[:10]:
                    lines.append(f"  - {item}")
    return {"changes": changes, "markdown": "\n".join(lines) + "\n"}


def build_retest_queue(triage_findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build retest task specs for findings flagged for retest."""
    queue: list[dict[str, Any]] = []
    for f in triage_findings:
        if f.get("retest_required"):
            queue.append({
                "finding_id": f.get("finding_id"),
                "asset": f.get("asset"),
                "category": f.get("category"),
                "severity": f.get("severity"),
                "module": _module_for_category(f.get("category", "")),
            })
    return queue


def _module_for_category(category: str) -> str:
    return {
        "headers": "header_review", "reflection": "param_probe",
        "access_control": "access_control", "api": "api_review",
        "info_disclosure": "http_probe", "anomaly": "http_probe",
    }.get(category, "http_probe")


class ContinuousMonitor:
    """Detect changes, emit delta reports, and schedule retests via JobManager."""

    def __init__(self, manager: Any = None) -> None:
        self._manager = manager

    @property
    def manager(self) -> Any:
        if self._manager is None:
            from wth_ai.jobs.manager import JobManager
            self._manager = JobManager()
        return self._manager

    def changes(self, target: str) -> dict[str, Any]:
        """Diff the two most recent completed recon jobs for ``target``."""
        store = self.manager.store
        jobs = [j for j in store.list_jobs(type="recon", status="completed", limit=50)
                if (j.target or "") == target]
        if len(jobs) < 2:
            return {"changed": False, "reason": "need >=2 completed recon runs"}
        new = _load_normalized(store, jobs[0].job_id)
        old = _load_normalized(store, jobs[1].job_id)
        if old is None or new is None:
            return {"changed": False, "reason": "missing normalized results"}
        report = delta_report(old, new)
        changed = bool(report["changes"].get("changed"))
        if changed:
            try:
                self.manager.bus.emit("recon.change_detected", data={
                    "target": target,
                    "changes": {k: v for k, v in report["changes"].items()
                                if isinstance(v, list) and v},
                })
            except Exception:  # noqa: BLE001
                pass
        return {"changed": changed, **report}

    def schedule_retests(self, target: str, triage_findings: list[dict[str, Any]],
                         *, mode: str = "safe-active") -> list[str]:
        """Create executor retest jobs for retest-flagged findings (feedback edge L2->G)."""
        queue = build_retest_queue(triage_findings)
        job_ids: list[str] = []
        for spec in queue:
            j = self.manager.create_job(
                "executor", target=target, execution_mode=mode, created_by="retest",
                metadata={"authorized": True, "retest_of": spec.get("finding_id"),
                          "focus_module": spec.get("module")},
            )
            if j.status != "failed":
                job_ids.append(j.job_id)
        return job_ids
