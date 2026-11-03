"""Evidence Vault — request/response pairs linked to graph nodes and chains."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from wth_ai.models import utc_now


class EvidenceVault:
    """Persist redacted request/response evidence per finding."""

    def __init__(self, session_id: str, base_dir: Path | None = None) -> None:
        home = base_dir or Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai")) / "data" / "engagements"
        self.dir = home / session_id / "evidence"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        self._items: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def _save(self) -> None:
        self.index_path.write_text(json.dumps(self._items, ensure_ascii=False, indent=2), encoding="utf-8")

    def redact(self, text: str) -> str:
        """WU-203: delegate to canonical `wth_ai.redaction.redact_secrets`
        (single source of truth; also adds AWS-key coverage vs. the old local
        copy)."""
        from wth_ai.redaction import redact_secrets

        return redact_secrets(text)

    def store(
        self,
        *,
        finding_id: str,
        chain_step: str = "",
        graph_node_id: str = "",
        request: dict[str, Any],
        response: dict[str, Any],
        evidence_schema: dict[str, Any] | None = None,
    ) -> str:
        eid = f"EV-{uuid4().hex[:10]}"
        entry = {
            "id": eid,
            "finding_id": finding_id,
            "chain_step": chain_step,
            "graph_node_id": graph_node_id,
            "timestamp": utc_now(),
            "request": {k: self.redact(str(v)) if isinstance(v, str) else v for k, v in request.items()},
            "response": {k: self.redact(str(v)) if isinstance(v, str) else v for k, v in response.items()},
            "evidence_schema": evidence_schema or {},
        }
        self._items.append(entry)
        (self.dir / f"{eid}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        self._save()
        return eid

    def store_from_executor(self, task_id: str, pairs: list[dict[str, Any]], schema: dict[str, Any] | None = None) -> list[str]:
        ids: list[str] = []
        for i, pair in enumerate(pairs):
            eid = self.store(
                finding_id=task_id,
                chain_step=pair.get("step", f"step_{i}"),
                request={"method": pair.get("method", "GET"), "url": pair.get("url", "")},
                response={"status": pair.get("response_status", 0), "body": pair.get("body_snippet", "")},
                evidence_schema=schema,
            )
            ids.append(eid)
        return ids

    def validate_schema(self, finding_id: str, schema: dict[str, Any]) -> tuple[bool, list[str]]:
        """Gate: evidence must match schema requirements."""
        items = [e for e in self._items if e["finding_id"] == finding_id]
        missing: list[str] = []
        if not items:
            missing.append("no_request_response_pairs")
            return False, missing
        proof = schema.get("proof", "")
        if proof and not any(proof in json.dumps(e) for e in items):
            missing.append(f"proof_type:{proof}")
        if schema.get("type") and not any(schema["type"] in json.dumps(e) for e in items):
            missing.append(f"vuln_type:{schema['type']}")
        return len(missing) == 0, missing

    def export_poc_block(self, finding_id: str) -> str:
        items = [e for e in self._items if e["finding_id"] == finding_id]
        if not items:
            return "_No stored request/response pairs._"
        lines = ["### Proof of Concept", ""]
        for e in items[:3]:
            req = e["request"]
            resp = e["response"]
            lines.append("```http")
            lines.append(f"{req.get('method', 'GET')} {req.get('url', '')}")
            lines.append("```")
            lines.append("")
            lines.append(f"**Response:** `{resp.get('status', '?')}`")
            body = resp.get("body", "")
            if body:
                lines.append("```")
                lines.append(str(body)[:1500])
                lines.append("```")
            lines.append("")
        return "\n".join(lines)

    def count(self) -> int:
        return len(self._items)
