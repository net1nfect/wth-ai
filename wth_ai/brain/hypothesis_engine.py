"""Rule-based hypothesis generation from asset graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from wth_ai.graph.asset_graph import AssetGraph
from wth_ai.graph.models import NodeType
from wth_ai.models import tech_stack_blob


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


class HypothesisEngine:
    """Generate hypotheses from graph + rules before LLM."""

    def __init__(self, rules_path: Path | None = None) -> None:
        path = rules_path or Path(__file__).parent / "rules.yaml"
        self.rules: list[dict[str, Any]] = []
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            self.rules = raw.get("rules", [])

    def generate(self, graph: AssetGraph, tech_stack: dict[str, str] | None = None) -> list[dict[str, Any]]:
        tech_blob = tech_stack_blob(tech_stack)
        hypotheses: list[dict[str, Any]] = []
        seen: set[str] = set()

        for rule in self.rules:
            matched = self._match_rule(rule, graph, tech_blob)
            for m in matched:
                key = m.get("title", "")
                if key in seen:
                    continue
                seen.add(key)
                hypotheses.append(m)

        # Graph-native rules
        for p in graph.query_high_value_params(20):
            d = p.data
            param = _text(d.get("param", ""))
            url = _text(d.get("url", ""))
            if any(k in param.lower() for k in ("id", "user", "uuid")):
                title = f"BOLA/IDOR on {param} @ {url[:60]}"
                if title not in seen:
                    seen.add(title)
                    hypotheses.append({
                        "id": f"HYP-GRAPH-{len(hypotheses)+1}",
                        "title": title,
                        "component": url,
                        "vuln_class": "idor",
                        "priority": p.priority,
                        "why_suspicious": f"High-value param `{param}` on API-like surface",
                        "safe_test": "Swap object ID between user_a and user_b accounts",
                        "evidence_needed": "User B receives User A object data",
                        "source": "graph_engine",
                    })
            if param.lower() in ("url", "redirect", "next", "callback", "dest"):
                title = f"SSRF/Open redirect via {param}"
                if title not in seen:
                    seen.add(title)
                    hypotheses.append({
                        "id": f"HYP-GRAPH-{len(hypotheses)+1}",
                        "title": title,
                        "component": url,
                        "vuln_class": "ssrf",
                        "priority": 8,
                        "why_suspicious": f"URL/redirect param `{param}`",
                        "safe_test": "Probe with internal URL placeholder",
                        "evidence_needed": "Server fetches internal resource",
                        "source": "graph_engine",
                    })

        hypotheses.sort(key=lambda h: -h.get("priority", 5))
        return hypotheses[:30]

    def _match_rule(self, rule: dict[str, Any], graph: AssetGraph, tech_blob: str) -> list[dict[str, Any]]:
        when = rule.get("when", {})
        if when.get("tech_contains"):
            if not any(_text(t).lower() in tech_blob for t in when["tech_contains"]):
                return []

        out: list[dict[str, Any]] = []
        param_names = when.get("param_matches", [])
        auth_required = when.get("auth_required")

        for n in graph.nodes.values():
            if n.type != NodeType.PARAMETER:
                continue
            param = _text(n.data.get("param", ""))
            if param_names and not any(p in param.lower() for p in param_names):
                continue
            url = _text(n.data.get("url", ""))
            ep_key = url
            auth = n.data.get("auth_required", "/api/" in url)
            if auth_required is not None and bool(auth) != bool(auth_required):
                continue
            title = rule.get("hypothesis", "").format(endpoint=url, param=param)
            out.append({
                "id": f"HYP-{rule.get('id', 'rule')}-{len(out)+1}",
                "title": title,
                "component": url,
                "vuln_class": rule.get("vuln_class", "unknown"),
                "priority": rule.get("priority", 5),
                "why_suspicious": rule.get("why", title),
                "safe_test": rule.get("safe_test", ""),
                "evidence_needed": rule.get("evidence_needed", ""),
                "chain_steps": rule.get("chain", []),
                "source": f"rule:{rule.get('id')}",
            })
        return out[:10]
