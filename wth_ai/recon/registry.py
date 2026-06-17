cursor/fase5-recon-platform-93fe
"""Recon tool registry: catalog + availability + enable/disable overrides.

Availability rules (never raises, never crashes on missing tools):
- ``builtin``  -> always available (pure-Python).
- ``external`` -> available if any ``binary_names`` is on PATH / in the WTH tools bin.
- ``api``      -> available if its vault key resolves (checked lazily, optional).
- ``manual``/``stub`` -> never auto-available (operator/implementation required).

Enable/disable overrides persist to ``$WTH_HOME/config/recon_tools.yaml`` so the
operator can opt-in to high-risk tools or opt-out of others.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from wth_ai.recon.model import ToolSpec
from wth_ai.recon.tools.catalog import build_catalog
from wth_ai.workspace import wth_home


def _tools_bin() -> Path:
    return wth_home() / "tools" / "bin"


class ReconToolRegistry:
    """Loads the 200+ tool catalog and answers availability/enablement."""

    def __init__(self, *, overrides_path: Path | None = None) -> None:
        self.tools: dict[str, ToolSpec] = build_catalog()
        self.overrides_path = overrides_path or (wth_home() / "config" / "recon_tools.yaml")
        self._overrides: dict[str, bool] = self._load_overrides()
        self._vault_keys: set[str] | None = None  # lazily cached once per instance

    # --- overrides persistence ---
    def _load_overrides(self) -> dict[str, bool]:
        if self.overrides_path.exists():
            try:
                data = yaml.safe_load(self.overrides_path.read_text(encoding="utf-8")) or {}
                enabled = data.get("enabled", {})
                if isinstance(enabled, dict):
                    return {str(k): bool(v) for k, v in enabled.items()}
            except (OSError, yaml.YAMLError):
                pass
        return {}

    def _save_overrides(self) -> None:
        try:
            self.overrides_path.parent.mkdir(parents=True, exist_ok=True)
            self.overrides_path.write_text(
                yaml.safe_dump({"enabled": self._overrides}, sort_keys=True),
                encoding="utf-8",
            )
        except OSError:
            pass

    # --- lookups ---
    def get(self, name: str) -> ToolSpec | None:
        return self.tools.get(name)

    def __len__(self) -> int:
        return len(self.tools)

    def by_category(self, category: str) -> list[ToolSpec]:
        return [s for s in self.tools.values() if s.category == category]

    def by_capability(self, capability: str) -> list[ToolSpec]:
        return [s for s in self.tools.values() if capability in s.capabilities]

    # --- availability (never raises) ---
    def is_available(self, name: str) -> bool:
        spec = self.tools.get(name)
        if spec is None:
            return False
        mode = spec.execution_mode
        if mode == "builtin":
            return True
        if mode == "external":
            return self._external_available(spec)
        if mode == "api":
            return self._api_key_present(spec)
        return False  # manual/stub

    def _external_available(self, spec: ToolSpec) -> bool:
        bin_dir = _tools_bin()
        for b in spec.binary_names or [spec.name]:
            if shutil.which(b):
                return True
            cand = bin_dir / b
            if cand.exists():
                return True
        return False

    def _api_key_present(self, spec: ToolSpec) -> bool:
        ref = spec.api_key_ref or ""
        # vault ref form: "vault:<name>"; check env first, then cached vault keys.
        if not ref:
            return False
        name = ref.split(":", 1)[1] if ":" in ref else ref
        if os.environ.get(name.upper()):
            return True
        return name in self._vault_key_set()

    def _vault_key_set(self) -> set[str]:
        """Vault key names, decrypted once and cached for this registry instance."""
        if self._vault_keys is None:
            keys: set[str] = set()
            try:
                from wth_ai.vault import SecretVault

                vault = SecretVault()
                if vault.exists() and vault.can_open():
                    keys = set(vault.list_keys())
            except Exception:  # noqa: BLE001 — vault optional/locked
                keys = set()
            self._vault_keys = keys
        return self._vault_keys

    # --- enablement ---
    def is_enabled(self, name: str) -> bool:
        spec = self.tools.get(name)
        if spec is None:
            return False
        return self._overrides.get(name, spec.enabled_by_default)

    def enable(self, name: str) -> bool:
        if name not in self.tools:
            return False
        self._overrides[name] = True
        self._save_overrides()
        return True

    def disable(self, name: str) -> bool:
        if name not in self.tools:
            return False
        self._overrides[name] = False
        self._save_overrides()
        return True

    # --- bulk queries ---
    def available(self) -> list[ToolSpec]:
        return [s for s in self.tools.values() if self.is_available(s.name)]

    def missing(self) -> list[ToolSpec]:
        return [
            s for s in self.tools.values()
            if s.execution_mode in ("external", "api") and not self.is_available(s.name)
        ]

    def install_hint(self, name: str) -> str:
        spec = self.tools.get(name)
        if spec is None:
            return ""
        if spec.execution_mode == "api":
            return f"set {spec.api_key_ref} in vault"
        if spec.execution_mode == "external":
            return f"install '{spec.binary_names[0] if spec.binary_names else name}' on PATH"
        if spec.execution_mode in ("manual", "stub"):
            return "operator-provided / not auto-runnable"
        return ""

    def summary(self) -> dict[str, Any]:
        total = len(self.tools)
        avail = len(self.available())
        return {
            "total": total,
            "available": avail,
            "missing": total - avail,
            "builtin": sum(1 for s in self.tools.values() if s.execution_mode == "builtin"),
            "external": sum(1 for s in self.tools.values() if s.execution_mode == "external"),
            "api": sum(1 for s in self.tools.values() if s.execution_mode == "api"),
            "manual": sum(1 for s in self.tools.values() if s.execution_mode in ("manual", "stub")),
            "disabled_by_default": sum(1 for s in self.tools.values() if not s.enabled_by_default),
        }

    def format_list(self) -> str:
        lines = [f"# Recon tools ({len(self.tools)} total)"]
        by_cat: dict[str, list[ToolSpec]] = {}
        for s in self.tools.values():
            by_cat.setdefault(s.category, []).append(s)
        from wth_ai.recon.tools.catalog import CATEGORY_LABELS

        for cat, specs in by_cat.items():
            lines.append(f"\n## {CATEGORY_LABELS.get(cat, cat)} ({len(specs)})")
            for s in sorted(specs, key=lambda x: x.name):
                status = "available" if self.is_available(s.name) else "missing"
                en = "enabled" if self.is_enabled(s.name) else "disabled"
                risk = f" risk={s.risk_level}" if s.risk_level != "safe" else ""
                lines.append(
                    f"- {s.name} [{status}] category={s.category} "
                    f"mode={s.execution_mode} {en}{risk}"
                )
        return "\n".join(lines)
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
main
