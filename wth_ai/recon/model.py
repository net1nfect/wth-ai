"""Fase 5 recon platform data models.

These models are *additive* to ``wth_ai/models.py`` (which keeps the legacy
``AttackSurface`` used by the phase engine). They describe the new real-time
recon platform: tool specifications, tool runs, plans, and normalized assets.

All recon here is **recon-only / non-destructive**. Risk metadata on a
``ToolSpec`` is advisory for the planner and registry; the runner additionally
enforces safety (no ``shell=True``, timeouts, output caps, secret redaction).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# --- enumerations expressed as plain string tuples (no hard enum dep) ---

EXECUTION_MODES: tuple[str, ...] = ("external", "builtin", "api", "manual", "stub")
RISK_LEVELS: tuple[str, ...] = ("safe", "medium", "high")
PROFILES: tuple[str, ...] = ("budget", "default", "recon-only", "high-quality", "local")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Tool specification
# --------------------------------------------------------------------------- #
@dataclass
class ToolSpec:
    """Metadata for a single recon tool (one of 200+ in the catalog)."""

    name: str
    category: str
    capabilities: list[str] = field(default_factory=list)
    execution_mode: str = "stub"  # external|builtin|api|manual|stub
    binary_names: list[str] = field(default_factory=list)
    enabled_by_default: bool = True
    risk_level: str = "safe"  # safe|medium|high
    requires_network: bool = True
    requires_api_key: bool = False
    api_key_ref: str | None = None
    allowed_profiles: list[str] = field(
        default_factory=lambda: ["recon-only", "default", "high-quality"]
    )
    output_types: list[str] = field(default_factory=list)
    rate_limited: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "capabilities": list(self.capabilities),
            "execution_mode": self.execution_mode,
            "binary_names": list(self.binary_names),
            "enabled_by_default": self.enabled_by_default,
            "risk_level": self.risk_level,
            "requires_network": self.requires_network,
            "requires_api_key": self.requires_api_key,
            "api_key_ref": self.api_key_ref,
            "allowed_profiles": list(self.allowed_profiles),
            "output_types": list(self.output_types),
            "rate_limited": self.rate_limited,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolSpec":
        return cls(
            name=d["name"],
            category=d.get("category", "misc"),
            capabilities=list(d.get("capabilities", [])),
            execution_mode=d.get("execution_mode", "stub"),
            binary_names=list(d.get("binary_names", [])),
            enabled_by_default=bool(d.get("enabled_by_default", True)),
            risk_level=d.get("risk_level", "safe"),
            requires_network=bool(d.get("requires_network", True)),
            requires_api_key=bool(d.get("requires_api_key", False)),
            api_key_ref=d.get("api_key_ref"),
            allowed_profiles=list(d.get("allowed_profiles", ["recon-only", "default", "high-quality"])),
            output_types=list(d.get("output_types", [])),
            rate_limited=bool(d.get("rate_limited", True)),
            notes=d.get("notes", ""),
        )


# --------------------------------------------------------------------------- #
# Tool run result
# --------------------------------------------------------------------------- #
@dataclass
class ToolRun:
    """Outcome of executing a single tool (builtin or external)."""

    tool: str
    status: str = "skipped"  # success|skipped|failed|timeout
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    output_path: str | None = None
    error: str | None = None
    normalized_items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "output_path": self.output_path,
            "error": self.error,
            "normalized_items": self.normalized_items,
        }


# --------------------------------------------------------------------------- #
# Recon plan
# --------------------------------------------------------------------------- #
@dataclass
class PlannedTool:
    tool: str
    category: str
    execution_mode: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "category": self.category,
            "execution_mode": self.execution_mode,
            "reason": self.reason,
        }


@dataclass
class SkippedTool:
    tool: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "reason": self.reason}


@dataclass
class ReconPlan:
    target: str
    target_type: str = "domain"  # domain|url|ip|local
    profile: str = "recon-only"
    realtime: bool = False
    selected: list[PlannedTool] = field(default_factory=list)
    skipped: list[SkippedTool] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    adaptive_profile: str | None = None
    matched_rules: list[str] = field(default_factory=list)
    boost_categories: list[str] = field(default_factory=list)
    skip_categories: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "target_type": self.target_type,
            "profile": self.profile,
            "realtime": self.realtime,
            "selected_count": len(self.selected),
            "skipped_count": len(self.skipped),
            "selected": [t.to_dict() for t in self.selected],
            "skipped": [t.to_dict() for t in self.skipped],
            "created_at": self.created_at,
            "adaptive_profile": self.adaptive_profile,
            "matched_rules": self.matched_rules,
            "boost_categories": self.boost_categories,
            "skip_categories": self.skip_categories,
        }


# --------------------------------------------------------------------------- #
# Normalized asset models
# --------------------------------------------------------------------------- #
@dataclass
class _Base:
    sources: list[str] = field(default_factory=list)
    confidence: float = 0.5

    def merge_source(self, tool: str) -> None:
        if tool and tool not in self.sources:
            self.sources.append(tool)
            # more independent confirmations -> higher confidence (capped)
            self.confidence = min(1.0, 0.5 + 0.15 * len(self.sources))


@dataclass
class DomainAsset(_Base):
    host: str = ""

    def key(self) -> str:
        return self.host.lower()

    def to_dict(self) -> dict[str, Any]:
        return {"host": self.host, "sources": self.sources, "confidence": round(self.confidence, 3)}


@dataclass
class DNSRecord(_Base):
    host: str = ""
    record_type: str = "A"
    value: str = ""

    def key(self) -> str:
        return f"{self.host.lower()}|{self.record_type}|{self.value}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "record_type": self.record_type,
            "value": self.value,
            "sources": self.sources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class URLAsset(_Base):
    url: str = ""

    def key(self) -> str:
        return self.url.rstrip("/").lower()

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "sources": self.sources, "confidence": round(self.confidence, 3)}


@dataclass
class HTTPProbe(_Base):
    url: str = ""
    status: int = 0
    title: str = ""
    server: str = ""
    content_type: str = ""
    length: int = 0

    def key(self) -> str:
        return self.url.rstrip("/").lower()

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status": self.status,
            "title": self.title,
            "server": self.server,
            "content_type": self.content_type,
            "length": self.length,
            "sources": self.sources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class HeaderPosture(_Base):
    url: str = ""
    missing: list[str] = field(default_factory=list)
    present: dict[str, str] = field(default_factory=dict)

    def key(self) -> str:
        return self.url.rstrip("/").lower()

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "missing": self.missing,
            "present": self.present,
            "sources": self.sources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class CookiePosture(_Base):
    url: str = ""
    name: str = ""
    secure: bool = False
    httponly: bool = False
    samesite: str = ""

    def key(self) -> str:
        return f"{self.url.rstrip('/').lower()}|{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "name": self.name,
            "secure": self.secure,
            "httponly": self.httponly,
            "samesite": self.samesite,
            "sources": self.sources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class TLSInfo(_Base):
    host: str = ""
    subject: str = ""
    issuer: str = ""
    not_after: str = ""
    san: list[str] = field(default_factory=list)

    def key(self) -> str:
        return self.host.lower()

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "subject": self.subject,
            "issuer": self.issuer,
            "not_after": self.not_after,
            "san": self.san,
            "sources": self.sources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class TechnologyFingerprint(_Base):
    host: str = ""
    technology: str = ""
    version: str = ""

    def key(self) -> str:
        return f"{self.host.lower()}|{self.technology.lower()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "technology": self.technology,
            "version": self.version,
            "sources": self.sources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class Endpoint(_Base):
    url: str = ""
    method: str = "GET"
    kind: str = "web"  # web|api|graphql|docs

    def key(self) -> str:
        return f"{self.method}|{self.url.rstrip('/').lower()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "method": self.method,
            "kind": self.kind,
            "sources": self.sources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class Parameter(_Base):
    url: str = ""
    name: str = ""
    interest: int = 0

    def key(self) -> str:
        return f"{self.url.rstrip('/').lower()}|{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "name": self.name,
            "interest": self.interest,
            "sources": self.sources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class JSIntel(_Base):
    url: str = ""
    endpoints: list[str] = field(default_factory=list)

    def key(self) -> str:
        return self.url.rstrip("/").lower()

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "endpoints": self.endpoints,
            "sources": self.sources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class SecretCandidateMasked(_Base):
    source: str = ""
    secret_type: str = ""
    masked: str = ""  # never the full secret

    def key(self) -> str:
        return f"{self.source}|{self.secret_type}|{self.masked}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "secret_type": self.secret_type,
            "masked": self.masked,
            "sources": self.sources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class CloudCandidate(_Base):
    provider: str = ""
    asset: str = ""

    def key(self) -> str:
        return f"{self.provider.lower()}|{self.asset.lower()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "asset": self.asset,
            "sources": self.sources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class FindingCandidate(_Base):
    title: str = ""
    asset: str = ""
    category: str = "info"
    severity_hint: str = "info"
    detail: str = ""

    def key(self) -> str:
        return f"{self.category}|{self.asset}|{self.title}".lower()

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "asset": self.asset,
            "category": self.category,
            "severity_hint": self.severity_hint,
            "detail": self.detail,
            "sources": self.sources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class PriorityTarget:
    asset: str
    score: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"asset": self.asset, "score": self.score, "reasons": self.reasons}
