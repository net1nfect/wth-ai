"""Action memory — prevent useless repeated tool calls within a session."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wth_ai.models import utc_now
from wth_ai.workspace import wth_home


INFO_TOOLS_ONCE = frozenset({"scope_show", "recon_list_tools", "list_findings", "query_graph"})


def _normalize_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    # Keep only high-signal keys. Avoid hashing huge blobs/bodies.
    if tool == "recon_list_tools":
        return {
            "category": args.get("category"),
            "available_only": bool(args.get("available_only")),
        }
    if tool == "recon_run_tool":
        return {"tool_name": args.get("tool_name"), "target": args.get("target")}
    if tool == "http_probe":
        return {"url": args.get("url"), "method": args.get("method")}
    if tool == "run_shell":
        cmd = args.get("cmd") or args.get("command") or ""
        return {"cmd": str(cmd)[:200]}
    # Default: shallow, JSON-safe, length-limited.
    out: dict[str, Any] = {}
    for k, v in (args or {}).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v if not isinstance(v, str) else v[:200]
        elif isinstance(v, (list, tuple)) and len(v) <= 8:
            out[k] = [str(x)[:80] for x in v]
        else:
            out[k] = str(v)[:120]
    return out


def _fingerprint(tool: str, args: dict[str, Any]) -> str:
    norm = _normalize_args(tool, args)
    raw = json.dumps(norm, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{tool}:{raw}".encode("utf-8")).hexdigest()[:16]


def _status_from_result(result: str) -> str:
    r = (result or "").strip()
    low = r.lower()
    if r.startswith("ERROR:") or low.startswith("error:"):
        return "error"
    if r in {"[]", "{}", ""}:
        return "empty"
    if "graph empty" in low or "no findings" in low:
        return "empty"
    if r.startswith("SKIP (memory):"):
        return "blocked"
    return "ok"


@dataclass(frozen=True)
class ActionRecord:
    tool: str
    fp: str
    status: str
    summary: str
    at: str
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "fp": self.fp,
            "status": self.status,
            "summary": self.summary,
            "at": self.at,
            "duration_ms": self.duration_ms,
        }


class ActionMemory:
    def __init__(self, session_id: str, *, base_dir: Path | None = None) -> None:
        self.session_id = session_id
        home = base_dir or (wth_home() / "data" / "engagements")
        self.path = home / session_id / "actions.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[ActionRecord] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            recs = raw.get("records", [])
            self.records = [
                ActionRecord(
                    tool=r.get("tool", ""),
                    fp=r.get("fp", ""),
                    status=r.get("status", ""),
                    summary=r.get("summary", ""),
                    at=r.get("at", ""),
                    duration_ms=int(r.get("duration_ms", 0) or 0),
                )
                for r in recs
                if isinstance(r, dict)
            ]
        except Exception:
            self.records = []

    def save(self) -> None:
        self.path.write_text(
            json.dumps(
                {"session_id": self.session_id, "updated_at": utc_now(), "records": [r.to_dict() for r in self.records[-200:]]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def record(self, tool: str, args: dict[str, Any], result: str, *, duration_ms: int = 0) -> None:
        fp = _fingerprint(tool, args)
        status = _status_from_result(result)
        summary = (result or "").replace("\n", " ").strip()[:200]
        rec = ActionRecord(tool=tool, fp=fp, status=status, summary=summary, at=utc_now(), duration_ms=duration_ms)
        self.records = [*self.records, rec]
        self.save()

    def should_block(self, tool: str, args: dict[str, Any]) -> tuple[bool, str]:
        fp = _fingerprint(tool, args)
        recent = [r for r in self.records[-50:] if r.tool == tool and r.fp == fp]
        if not recent:
            return False, ""

        # Info tools: once per session unless user explicitly asks to retry.
        if tool in INFO_TOOLS_ONCE and any(r.status == "ok" for r in recent):
            return True, f"{tool} already succeeded for this session"

        bad = [r for r in recent if r.status in {"error", "empty"}]
        if len(bad) >= 2:
            return True, f"{tool} repeated {len(bad)}x with status={bad[-1].status}"

        # Simple loop detection: same tool 3+ times in last 6 calls.
        window = self.records[-6:]
        if sum(1 for r in window if r.tool == tool) >= 3:
            return True, f"loop detected: {tool} repeated in last 6 actions"

        return False, ""

    def context_block(self, *, limit: int = 12) -> str:
        if not self.records:
            return ""
        rows = self.records[-limit:]
        lines = ["=== ACTION MEMORY (do not repeat) ==="]
        for r in rows:
            lines.append(f"- {r.tool} [{r.status}] {r.summary}")
        lines.append("=== END ACTION MEMORY ===")
        return "\n".join(lines)

