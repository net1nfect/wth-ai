"""Adaptive recon rules — maps ``recon_rules.yaml`` to tool category boosts/skips."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from wth_ai.models import tech_stack_blob

# Legacy 12-phase IDs → Fase 5 tool categories.
PHASE_CATEGORIES: dict[int, frozenset[str]] = {
    1: frozenset({"subdomain"}),
    2: frozenset({"dns"}),
    3: frozenset({"http_probe"}),
    4: frozenset({"crawl", "archive"}),
    5: frozenset({"param"}),
    6: frozenset({"js", "code_intel"}),
    7: frozenset({"cloud"}),
    8: frozenset({"tls"}),
    9: frozenset({"core"}),
    10: frozenset({"api_schema", "intel"}),
}

_API_HINT = ("/api/", "/v1/", "/v2/", "/graphql", "/rest/")


@dataclass
class ReconSignals:
    tech_stack: dict[str, str] | None = None
    api_endpoints: int = 0
    live_hosts: int = 0
    urls: int = 0
    headers_blob: str = ""
    url_blob: str = ""

    @classmethod
    def from_target(cls, target: str) -> ReconSignals:
        return cls(url_blob=(target or "").lower())

    @classmethod
    def from_normalized(cls, data: dict[str, Any]) -> ReconSignals:
        endpoints = list(data.get("endpoints") or [])
        probes = list(data.get("http_probes") or [])
        urls_raw = list(data.get("alive_urls") or [])
        url_strings = [
            u if isinstance(u, str) else str(u.get("url", ""))
            for u in urls_raw
        ]
        url_strings.extend(
            p.get("url", "") if isinstance(p, dict) else str(p) for p in probes
        )
        url_strings.extend(
            e.get("url", "") if isinstance(e, dict) else str(e) for e in endpoints
        )
        url_blob = " ".join(url_strings).lower()
        tech: dict[str, str] = {}
        for fp in data.get("fingerprints") or data.get("technologies") or []:
            if not isinstance(fp, dict):
                continue
            name = str(fp.get("name") or fp.get("tech") or "")
            if name:
                tech[name] = str(fp.get("version", ""))
        headers = data.get("headers") or data.get("header_postures") or []
        hblob = " ".join(
            str(h) if not isinstance(h, dict) else " ".join(str(v) for v in h.values())
            for h in headers
        ).lower()
        api_count = sum(
            1 for e in endpoints
            if _api_like(e.get("url", "") if isinstance(e, dict) else str(e))
        )
        return cls(
            tech_stack=tech or None,
            api_endpoints=api_count,
            live_hosts=len(probes),
            urls=len({u for u in url_strings if u}),
            headers_blob=hblob,
            url_blob=url_blob,
        )


def _api_like(url: str) -> bool:
    low = url.lower()
    return any(h in low for h in _API_HINT)


@dataclass
class AdaptiveMatch:
    profile: str = "generic_web"
    boost_categories: tuple[str, ...] = ()
    skip_categories: tuple[str, ...] = ()
    matched_rules: list[str] = field(default_factory=list)


class AdaptiveReconRules:
    """Evaluate YAML rules against recon signals (replaces legacy AdaptiveReconGraph)."""

    def __init__(self, rules_path: Path | str | None = None) -> None:
        path = Path(rules_path or Path(__file__).parent / "recon_rules.yaml")
        self._rules: list[dict[str, Any]] = []
        self._default_profile = "generic_web"
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            self._rules = list(raw.get("rules", []))
            self._default_profile = (raw.get("default") or {}).get("profile", "generic_web")

    def evaluate(self, signals: ReconSignals) -> AdaptiveMatch:
        boost: set[str] = set()
        skip: set[str] = set()
        profile = self._default_profile
        matched: list[str] = []

        for rule in self._rules:
            if not self._matches(rule.get("when", {}), signals):
                continue
            matched.append(str(rule.get("id", "rule")))
            skip.update(self._phases_to_categories(rule.get("skip_phases", [])))
            boost.update(self._phases_to_categories(rule.get("boost_phases", [])))
            if rule.get("profile"):
                profile = str(rule["profile"])

        boost_order = tuple(
            c for c in _boost_order(boost) if c not in skip
        )
        return AdaptiveMatch(
            profile=profile,
            boost_categories=boost_order,
            skip_categories=tuple(sorted(skip)),
            matched_rules=matched,
        )

    def _phases_to_categories(self, phases: list[int]) -> set[str]:
        out: set[str] = set()
        for p in phases:
            out.update(PHASE_CATEGORIES.get(int(p), ()))
        return out

    def _matches(self, when: dict[str, Any], signals: ReconSignals) -> bool:
        if "tech_contains" in when:
            blob = tech_stack_blob(signals.tech_stack).lower()
            if not any(str(t).lower() in blob for t in when["tech_contains"]):
                return False
        if "api_endpoints_min" in when:
            if signals.api_endpoints < int(when["api_endpoints_min"]):
                return False
        if "live_hosts_max" in when:
            if signals.live_hosts > int(when["live_hosts_max"]):
                return False
        if "urls_max" in when:
            if signals.urls > int(when["urls_max"]):
                return False
        if "headers_contains" in when:
            blob = signals.headers_blob
            if not any(str(k).lower() in blob for k in when["headers_contains"]):
                return False
        if "url_contains" in when:
            blob = signals.url_blob
            if not any(str(k).lower() in blob for k in when["url_contains"]):
                return False
        return True


def _boost_order(boost: set[str]) -> list[str]:
    base = (
        "subdomain", "dns", "http_probe", "archive", "crawl", "param", "js",
        "code_intel", "cloud", "tls", "api_schema", "intel", "port", "screenshot",
    )
    ordered = [c for c in base if c in boost]
    ordered.extend(sorted(boost - set(ordered)))
    return ordered


def category_rank(category: str, match: AdaptiveMatch) -> int:
    """Lower rank = run earlier. Skipped categories return 999."""
    if category in match.skip_categories:
        return 999
    if category in match.boost_categories:
        return match.boost_categories.index(category)
    return 100


def sort_specs_by_adaptive(specs: list[Any], match: AdaptiveMatch) -> list[Any]:
    if not match.matched_rules:
        return specs
    return sorted(specs, key=lambda s: (category_rank(s.category, match), s.name))


def signals_from_normalizer(normalizer: Any) -> ReconSignals:
    """Build signals from a partial or complete ReconNormalizer."""
    try:
        return ReconSignals.from_normalized(normalizer.to_dict())
    except Exception:
        return ReconSignals()


def filter_specs_by_adaptive(specs: list[Any], match: AdaptiveMatch) -> list[Any]:
    if not match.skip_categories:
        return specs
    return [s for s in specs if s.category not in match.skip_categories]
