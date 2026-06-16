"""WTH-AI tool registry — 200+ security tools."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _user_tools_bin() -> Path:
    home = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))
    return home / "tools" / "bin"


def _which(binary: str) -> str | None:
    """Resolve binary — prefer ~/.wth-ai/tools/bin then system PATH."""
    user_bin = _user_tools_bin() / binary
    if user_bin.exists() and os.access(user_bin, os.X_OK):
        return str(user_bin)
    return shutil.which(binary)


@dataclass
class ToolSpec:
    id: str
    binary: str
    category: str
    phases: list[int] = field(default_factory=list)
    permission: str = "read_only"
    install: dict[str, str] = field(default_factory=dict)
    verify: str = ""

    @classmethod
    def from_dict(cls, tid: str, data: dict[str, Any]) -> ToolSpec:
        return cls(
            id=tid,
            binary=data.get("binary", tid),
            category=data.get("category", "recon.misc"),
            phases=data.get("phases", []),
            permission=data.get("permission", "read_only"),
            install=data.get("install", {}),
            verify=data.get("verify", f"{data.get('binary', tid)} --version"),
        )


class ToolRegistry:
    """Load and query the tool manifest."""

    def __init__(self, manifest_path: Path | None = None) -> None:
        if manifest_path is None:
            manifest_path = Path(__file__).resolve().parent / "manifest.yaml"
        self.manifest_path = manifest_path
        self.tools: dict[str, ToolSpec] = {}
        self._load()

    def _load(self) -> None:
        if not self.manifest_path.exists():
            return
        raw = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8")) or {}
        for tid, data in (raw.get("tools") or {}).items():
            self.tools[tid] = ToolSpec.from_dict(tid, data)

    def is_installed(self, tool_id: str) -> bool:
        spec = self.tools.get(tool_id)
        if not spec:
            return False
        return _which(spec.binary) is not None

    def binary_path(self, tool_id: str) -> str | None:
        spec = self.tools.get(tool_id)
        if not spec:
            return None
        return _which(spec.binary)

    def installed_tools(self) -> list[str]:
        return [tid for tid in self.tools if self.is_installed(tid)]

    def missing_tools(self) -> list[str]:
        return [tid for tid in self.tools if not self.is_installed(tid)]

    def by_category(self, category: str) -> list[ToolSpec]:
        prefix = category if category.endswith(".") else category
        return [t for t in self.tools.values() if t.category.startswith(prefix) or t.category == category]

    def by_phase(self, phase: int) -> list[ToolSpec]:
        return [t for t in self.tools.values() if phase in t.phases]

    def categories(self) -> list[str]:
        cats = sorted({t.category for t in self.tools.values()})
        return cats

    def summary(self) -> dict[str, Any]:
        installed = len(self.installed_tools())
        total = len(self.tools)
        by_cat: dict[str, dict[str, int]] = {}
        for t in self.tools.values():
            by_cat.setdefault(t.category, {"installed": 0, "total": 0})
            by_cat[t.category]["total"] += 1
            if self.is_installed(t.id):
                by_cat[t.category]["installed"] += 1
        return {
            "total": total,
            "installed": installed,
            "missing": total - installed,
            "by_category": by_cat,
        }

    def format_report(self, category: str | None = None) -> str:
        s = self.summary()
        lines = [
            f"# WTH Tool Registry — {s['installed']}/{s['total']} installed",
            "",
        ]
        cats = [category] if category else sorted(s["by_category"].keys())
        for cat in cats:
            if cat not in s["by_category"]:
                continue
            info = s["by_category"][cat]
            lines.append(f"## {cat} ({info['installed']}/{info['total']})")
            for t in self.by_category(cat):
                status = "✓" if self.is_installed(t.id) else "✗"
                lines.append(f"- {status} `{t.id}` → `{t.binary}`")
            lines.append("")
        return "\n".join(lines)
