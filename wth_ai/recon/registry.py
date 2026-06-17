# wth_ai/recon/registry.py
"""
Tool Registry for Fase 5.1

Loads from the existing wth_ai/tools/manifest.yaml (215 tools).
Provides filtering, availability check, and planning support.

Respects AGENTS.md:
- No secrets in code
- CLI-friendly
- All high-risk tools respect permission levels
"""

import yaml
from pathlib import Path
from typing import List, Dict, Optional

from .models import ToolSpec


class ToolRegistry:
    """Central registry for all reconnaissance tools."""

    def __init__(self, manifest_path: Optional[Path] = None):
        if manifest_path is None:
            # Default location relative to package
            manifest_path = Path(__file__).parent.parent / "tools" / "manifest.yaml"
        self.manifest_path = manifest_path
        self._tools: Dict[str, ToolSpec] = {}
        self._load_manifest()

    def _load_manifest(self):
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {self.manifest_path}")

        with open(self.manifest_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        for name, spec in data.get("tools", {}).items():
            tool = ToolSpec(
                name=name,
                binary=spec.get("binary", name),
                category=spec.get("category", "misc"),
                phases=spec.get("phases", []),
                permission=spec.get("permission", "read_only"),
                enabled_by_default=spec.get("enabled_by_default", True),
                risk_level=spec.get("risk_level", "safe"),
                requires_network=spec.get("requires_network", True),
                requires_api_key=spec.get("requires_api_key", False),
                install=spec.get("install", {}),
                verify=spec.get("verify", ""),
                notes=spec.get("notes", ""),
            )
            self._tools[name] = tool

    def get_tool(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def list_tools(self, category: Optional[str] = None, enabled_only: bool = True) -> List[ToolSpec]:
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if category in t.category]
        if enabled_only:
            tools = [t for t in tools if t.enabled_by_default]
        return tools

    def get_tools_by_risk(self, max_risk: str = "safe") -> List[ToolSpec]:
        risk_order = {"safe": 0, "medium": 1, "high": 2}
        max_level = risk_order.get(max_risk, 0)
        return [
            t for t in self._tools.values()
            if risk_order.get(t.risk_level, 0) <= max_level
        ]

    def count(self) -> int:
        return len(self._tools)

    def get_categories(self) -> List[str]:
        return sorted({t.category for t in self._tools.values()})
