"""Adaptive Recon Graph — DAG-based phase selection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from wth_ai.models import AttackSurface


class AdaptiveReconGraph:
    """Evaluate recon rules after fingerprint to skip/boost phases."""

    def __init__(self, rules_path: str | Path | None = None) -> None:
        path = Path(rules_path or Path(__file__).parent / "recon_rules.yaml")
        self.rules_path = path
        self._rules: list[dict[str, Any]] = []
        self._default: dict[str, Any] = {"phases": list(range(1, 11)), "profile": "generic_web"}
        self._load()

    def _load(self) -> None:
        if not self.rules_path.exists():
            return
        raw = yaml.safe_load(self.rules_path.read_text(encoding="utf-8")) or {}
        self._rules = raw.get("rules", [])
        self._default = raw.get("default", self._default)

    def _match_rule(self, rule: dict[str, Any], surface: AttackSurface, interim: dict[str, Any]) -> bool:
        when = rule.get("when", {})
        summary = surface.summary()

        if "tech_contains" in when:
            tech_blob = " ".join(
                list(surface.tech_stack.values())
                + [t for h in surface.live_hosts for t in h.tech]
            ).lower()
            if not any(t.lower() in tech_blob for t in when["tech_contains"]):
                return False

        if "api_endpoints_min" in when:
            if summary.get("api_endpoints", 0) < when["api_endpoints_min"]:
                return False

        if "live_hosts_max" in when:
            if summary.get("live_hosts", 0) > when["live_hosts_max"]:
                return False

        if "urls_max" in when:
            if summary.get("urls", 0) > when["urls_max"]:
                return False

        if "headers_contains" in when:
            blob = " ".join(h.raw.lower() for h in surface.live_hosts)
            if not any(k.lower() in blob for k in when["headers_contains"]):
                return False

        if "url_contains" in when:
            blob = " ".join(surface.urls + surface.api_endpoints).lower()
            if not any(k.lower() in blob for k in when["url_contains"]):
                return False

        return True

    def plan_phases(self, surface: AttackSurface, interim: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return phase list, skip list, boost list, and tech profile after rules."""
        interim = interim or {}
        phases = set(self._default.get("phases", list(range(1, 11))))
        skip: set[int] = set()
        boost: set[int] = set()
        profile = self._default.get("profile", "generic_web")
        matched_rules: list[str] = []

        for rule in self._rules:
            if self._match_rule(rule, surface, interim):
                matched_rules.append(rule.get("id", "unknown"))
                skip.update(rule.get("skip_phases", []))
                boost.update(rule.get("boost_phases", []))
                if rule.get("profile"):
                    profile = rule["profile"]

        final = sorted(phases - skip)
        if not final:
            final = [1, 2, 3, 9, 10]

        return {
            "phases": final,
            "skip_phases": sorted(skip),
            "boost_phases": sorted(boost),
            "profile": profile,
            "matched_rules": matched_rules,
        }

    def reorder_for_boost(self, phases: list[int], boost: list[int]) -> list[int]:
        """Run boosted phases earlier within their natural groups."""
        boosted = [p for p in phases if p in boost]
        rest = [p for p in phases if p not in boost]
        return boosted + rest
