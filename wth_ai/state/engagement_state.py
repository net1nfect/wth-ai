"""State engagement: satu tempat untuk fakta teramati, hipotesis, dan verdict.

Ini bukan memory chat. Bentuknya sengaja kecil dan terstruktur supaya planner
bisa bekerja dari delta (observasi/hipotesis baru), bukan dari seluruh riwayat.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from wth_ai.state.normalizer import Observation, diff_observations
from wth_ai.workspace import wth_home

_ID_RE = re.compile(r"/(?:\d+|[0-9a-f]{8}-[0-9a-f-]{20,})(?=/|$)", re.IGNORECASE)
_STATE_VERSION = 1
_OBS_CAP = 500
_DEFAULT_COUNTERS = {
    "probes": 0,
    "verified": 0,
    "false_positives": 0,
    "needs_evidence": 0,
    "wasted": 0,
}


def endpoint_key(url: str) -> str:
    """Normalisasi URL: id numerik/UUID menjadi placeholder agar baseline nyambung."""
    path = url.split("?", 1)[0]
    return _ID_RE.sub("/{id}", path)


@dataclass
class Hypothesis:
    id: str
    vuln_class: str
    endpoint: str
    summary: str
    posterior: float = 0.0
    parent_id: str = ""
    state: str = "open"  # open | verified | false_positive | needs_evidence
    reasons: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "vuln_class": self.vuln_class,
            "endpoint": self.endpoint,
            "summary": self.summary,
            "posterior": round(self.posterior, 4),
            "parent_id": self.parent_id,
            "state": self.state,
            "reasons": list(self.reasons),
            "evidence_refs": list(self.evidence_refs),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Hypothesis":
        return cls(
            id=str(data.get("id") or f"HYP-{uuid4().hex[:8]}"),
            vuln_class=str(data.get("vuln_class") or ""),
            endpoint=str(data.get("endpoint") or ""),
            summary=str(data.get("summary") or ""),
            posterior=float(data.get("posterior") or 0.0),
            parent_id=str(data.get("parent_id") or ""),
            state=str(data.get("state") or "open"),
            reasons=[str(r) for r in data.get("reasons") or []],
            evidence_refs=[str(r) for r in data.get("evidence_refs") or []],
            created_at=str(data.get("created_at") or ""),
        )


@dataclass
class EngagementState:
    target: str = ""
    engagement_id: str = ""
    observations: list[Observation] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    verified_findings: list[dict[str, Any]] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=lambda: dict(_DEFAULT_COUNTERS))
    turns_to_first_evidence: int | None = None
    chain_depth: int = 0
    created_at: str = ""
    updated_at: str = ""
    _baselines: dict[str, Observation] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.engagement_id:
            self.engagement_id = f"ENG-{uuid4().hex[:8]}"
        if not self.created_at:
            from datetime import datetime, timezone
            self.created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.updated_at = self.created_at
        if not self._baselines:
            for obs in self.observations:
                self._baselines.setdefault(self._key(obs), obs)

    @staticmethod
    def _key(obs: Observation) -> str:
        return f"{obs.method.upper()} {endpoint_key(obs.url)}"

    def _touch(self) -> None:
        from datetime import datetime, timezone
        self.updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # -- observasi ---------------------------------------------------------
    def observe(self, obs: Observation, *, baseline: bool = False) -> list[str]:
        """Simpan observasi; kembalikan daftar perubahan vs baseline endpoint."""
        self.counters["probes"] += 1
        key = self._key(obs)
        changes: list[str] = []
        if baseline or key not in self._baselines:
            self._baselines[key] = obs
        else:
            changes = diff_observations(self._baselines[key], obs)
            if changes:
                self.anomalies.append({
                    "obs_id": obs.obs_id,
                    "endpoint": key,
                    "role": obs.role,
                    "changes": changes,
                })
                self.anomalies = self.anomalies[-50:]
            elif obs.role == "probe":
                self.counters["wasted"] += 1
        self.observations.append(obs)
        if len(self.observations) > _OBS_CAP:
            self.observations = self.observations[-_OBS_CAP:]
        self._touch()
        return changes

    def observe_pairs(self, pairs: Iterable[Mapping[str, Any]], *, source: str = "executor") -> list[Observation]:
        """Rekam pasangan request/response executor; pair pertama dianggap baseline."""
        from wth_ai.state.normalizer import observation_from_pair

        recorded: list[Observation] = []
        for index, pair in enumerate(pairs):
            role = str(pair.get("role") or "")
            if not role:
                role = "baseline" if index == 0 else "probe"
            observation = observation_from_pair(pair, role=role, source=source)
            self.observe(observation)
            recorded.append(observation)
        return recorded

    def baseline_for(self, url: str, method: str = "GET") -> Observation | None:
        return self._baselines.get(f"{method.upper()} {endpoint_key(url)}")

    # -- hipotesis ---------------------------------------------------------
    def add_hypothesis(
        self,
        vuln_class: str,
        endpoint: str,
        summary: str,
        *,
        posterior: float = 0.0,
        parent_id: str = "",
    ) -> Hypothesis:
        hyp = Hypothesis(
            id=f"HYP-{uuid4().hex[:8]}",
            vuln_class=vuln_class,
            endpoint=endpoint,
            summary=summary,
            posterior=float(posterior),
            parent_id=parent_id,
            created_at=self.updated_at,
        )
        self.hypotheses.append(hyp)
        self._touch()
        return hyp

    def record_verdict(
        self,
        hypothesis_id: str,
        verdict: str,
        *,
        reasons: Iterable[str] = (),
        evidence_refs: Iterable[str] = (),
        finding_summary: str = "",
    ) -> Hypothesis | None:
        hyp = next((h for h in self.hypotheses if h.id == hypothesis_id), None)
        if hyp is None:
            return None
        hyp.reasons = [str(r) for r in reasons]
        hyp.evidence_refs = [str(r) for r in evidence_refs]
        if verdict == "verified":
            hyp.state = "verified"
            self.counters["verified"] += 1
            self.verified_findings.append({
                "hypothesis_id": hyp.id,
                "vuln_class": hyp.vuln_class,
                "endpoint": hyp.endpoint,
                "summary": finding_summary or hyp.summary,
                "evidence_refs": list(hyp.evidence_refs),
            })
            depth = self._chain_depth(hyp)
            self.chain_depth = max(self.chain_depth, depth)
            if self.turns_to_first_evidence is None:
                self.turns_to_first_evidence = len(self.observations)
        elif verdict == "false_positive":
            hyp.state = "false_positive"
            self.counters["false_positives"] += 1
        else:
            hyp.state = "needs_evidence"
            self.counters["needs_evidence"] += 1
        self._touch()
        return hyp

    def _chain_depth(self, hyp: Hypothesis, _seen: set[str] | None = None) -> int:
        seen = _seen or set()
        if hyp.id in seen:
            return 0
        seen.add(hyp.id)
        if not hyp.parent_id:
            return 1
        parent = next((h for h in self.hypotheses if h.id == hyp.parent_id), None)
        if parent is None or parent.state != "verified":
            return 1
        return 1 + self._chain_depth(parent, seen)

    def open_hypotheses(self, limit: int = 8) -> list[Hypothesis]:
        return sorted(
            [h for h in self.hypotheses if h.state == "open"],
            key=lambda h: h.posterior,
            reverse=True,
        )[:limit]

    # -- konteks & metrik --------------------------------------------------
    def metrics(self) -> dict[str, Any]:
        return {
            "probes": self.counters["probes"],
            "verified": self.counters["verified"],
            "false_positives": self.counters["false_positives"],
            "needs_evidence": self.counters["needs_evidence"],
            "wasted_probes": self.counters["wasted"],
            "turns_to_first_evidence": self.turns_to_first_evidence,
            "chain_depth": self.chain_depth,
            "hypotheses_open": len(self.open_hypotheses(limit=1000)),
        }

    def context(self, *, max_hypotheses: int = 8, max_anomalies: int = 5) -> str:
        m = self.metrics()
        lines = [
            f"## Engagement {self.engagement_id} — {self.target or '(target belum di-set)'}",
            (
                f"probes={m['probes']} verified={m['verified']} "
                f"false_positive={m['false_positives']} needs_evidence={m['needs_evidence']} "
                f"wasted={m['wasted_probes']} turns_to_first={m['turns_to_first_evidence']} "
                f"chain_depth={m['chain_depth']}"
            ),
        ]
        endpoints = sorted(self._baselines)
        if endpoints:
            lines.append("Endpoints teramati: " + ", ".join(endpoints[-10:]))
        if self.verified_findings:
            lines.append("Terverifikasi:")
            for item in self.verified_findings[-5:]:
                lines.append(
                    f"- [{item['vuln_class']}] {item['endpoint']} — {item['summary']}"
                )
        open_hyps = self.open_hypotheses(max_hypotheses)
        if open_hyps:
            lines.append("Hipotesis terbuka (posterior):")
            for hyp in open_hyps:
                lines.append(
                    f"- {hyp.id} [{hyp.vuln_class}] {hyp.endpoint} "
                    f"p={hyp.posterior:.2f} — {hyp.summary}"
                )
        if self.anomalies:
            lines.append("Anomali terakhir:")
            for anomaly in self.anomalies[-max_anomalies:]:
                lines.append(
                    f"- {anomaly['endpoint']} ({anomaly['role'] or 'obs'}): "
                    + "; ".join(anomaly["changes"])
                )
        return "\n".join(lines)

    # -- persistensi -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": _STATE_VERSION,
            "engagement_id": self.engagement_id,
            "target": self.target,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "observations": [o.to_dict() for o in self.observations],
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "anomalies": list(self.anomalies),
            "verified_findings": list(self.verified_findings),
            "counters": dict(self.counters),
            "turns_to_first_evidence": self.turns_to_first_evidence,
            "chain_depth": self.chain_depth,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EngagementState":
        state = cls(
            target=str(data.get("target") or ""),
            engagement_id=str(data.get("engagement_id") or ""),
            observations=[Observation.from_dict(o) for o in data.get("observations") or []],
            hypotheses=[Hypothesis.from_dict(h) for h in data.get("hypotheses") or []],
            anomalies=list(data.get("anomalies") or []),
            verified_findings=list(data.get("verified_findings") or []),
            counters={**_DEFAULT_COUNTERS, **dict(data.get("counters") or {})},
            turns_to_first_evidence=data.get("turns_to_first_evidence"),
            chain_depth=int(data.get("chain_depth") or 0),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )
        return state

    def state_path(self, state_dir: str | Path | None = None) -> Path:
        base = Path(state_dir) if state_dir else wth_home() / "state"
        return base / f"{self.engagement_id}.json"

    def save(self, state_dir: str | Path | None = None) -> Path:
        path = self.state_path(state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, engagement_id: str, state_dir: str | Path | None = None) -> "EngagementState":
        base = Path(state_dir) if state_dir else wth_home() / "state"
        path = base / f"{engagement_id}.json"
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
