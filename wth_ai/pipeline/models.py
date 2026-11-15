"""Model data pipeline probe — kecil, serializable, tanpa logika HTTP."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from wth_ai.state.normalizer import Observation


def _headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    return {str(k): str(v) for k, v in (headers or {}).items()}


@dataclass(frozen=True)
class Parameter:
    """Satu titik input yang bisa diuji, beserta lokasinya."""

    name: str
    location: str  # query | json | form
    value: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "location": self.location, "value": self.value}


@dataclass(frozen=True)
class RequestSpec:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    #: False untuk open-redirect: Location harus dibaca, bukan diikuti.
    follow_redirects: bool = True

    def to_probe(self) -> dict[str, Any]:
        data: dict[str, Any] = {"method": self.method.upper(), "url": self.url}
        if self.headers:
            data["headers"] = dict(self.headers)
        if self.body:
            data["body"] = self.body
        if not self.follow_redirects:
            data["follow_redirects"] = False
        return data


@dataclass
class Surface:
    """Endpoint + parameter inventory. Bukan daftar payload."""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    params: list[Parameter] = field(default_factory=list)

    @classmethod
    def from_target(
        cls,
        target: str,
        *,
        method: str = "GET",
        headers: Mapping[str, Any] | None = None,
        body: str = "",
    ) -> "Surface":
        url = str(target or "").strip()
        if url and not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        header_map = _headers(headers)
        ctype = next((v for k, v in header_map.items() if k.lower() == "content-type"), "").lower()
        params: list[Parameter] = []
        parsed = urlsplit(url)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            params.append(Parameter(key, "query", value))
        stripped = (body or "").lstrip()
        if "json" in ctype or stripped.startswith(("{", "[")):
            try:
                data = json.loads(body)
            except (TypeError, json.JSONDecodeError):
                data = None
            if isinstance(data, dict):
                for key in data:
                    params.append(Parameter(str(key), "json", str(data[key])))
        elif body:
            for key, value in parse_qsl(body, keep_blank_values=True):
                params.append(Parameter(key, "form", value))
        return cls(method=method.upper() or "GET", url=url, headers=header_map, body=body, params=params)

    def query_param(self, name: str) -> Parameter | None:
        return next((p for p in self.params if p.location == "query" and p.name == name), None)

    def original(self) -> RequestSpec:
        return RequestSpec(self.method, self.url, dict(self.headers), self.body)

    def request_for(self, parameter: Parameter, value: Any) -> RequestSpec:
        if parameter.location == "query":
            parts = urlsplit(self.url)
            pairs = parse_qsl(parts.query, keep_blank_values=True)
            out: list[tuple[str, str]] = []
            replaced = False
            for key, old in pairs:
                if key == parameter.name:
                    out.append((key, str(value)))
                    replaced = True
                else:
                    out.append((key, old))
            if not replaced:
                out.append((parameter.name, str(value)))
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(out, doseq=True), parts.fragment))
            return RequestSpec(self.method, url, dict(self.headers), self.body)

        if parameter.location == "json":
            try:
                data = json.loads(self.body)
            except (TypeError, json.JSONDecodeError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data[parameter.name] = value
            headers = {k: v for k, v in self.headers.items() if k.lower() != "content-type"}
            headers["Content-Type"] = "application/json"
            return RequestSpec(self.method, self.url, headers, json.dumps(data))

        if parameter.location == "form":
            pairs = parse_qsl(self.body, keep_blank_values=True)
            out = []
            replaced = False
            for key, old in pairs:
                if key == parameter.name:
                    out.append((key, str(value)))
                    replaced = True
                else:
                    out.append((key, old))
            if not replaced:
                out.append((parameter.name, str(value)))
            headers = {k: v for k, v in self.headers.items() if k.lower() != "content-type"}
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            return RequestSpec(self.method, self.url, headers, urlencode(out, doseq=True))

        return self.original()

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "params": [p.to_dict() for p in self.params],
        }


@dataclass(frozen=True)
class ProbePlan:
    title: str
    request: RequestSpec
    expected: tuple[str, ...] = ()
    rationale: str = ""
    role: str = "probe"
    risk: str = "low"
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "role": self.role,
            "risk": self.risk,
            "expected": list(self.expected),
            "request": self.request.to_probe(),
        }


@dataclass
class ProbeOutcome:
    plan: ProbePlan
    observation: Observation
    body: str = ""
    diff: list[str] = field(default_factory=list)

    @property
    def pair(self) -> dict[str, Any]:
        return {
            "role": self.plan.role,
            "method": self.observation.method,
            "url": self.observation.url,
            "response_status": self.observation.status,
            "body_snippet": (self.body or self.observation.snippet)[:4000],
            "response_headers": dict(self.observation.headers),
            "elapsed_ms": self.observation.elapsed_ms,
            "transcript_id": self.observation.transcript_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.plan.title,
            "role": self.plan.role,
            "status": self.observation.status,
            "url": self.observation.url,
            "diff": list(self.diff),
        }


@dataclass
class TechniqueResult:
    evidence: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    risk: str = "low"
    verified: bool = False
    verdicts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": list(self.evidence),
            "observations": list(self.observations),
            "risk": self.risk,
            "verified": self.verified,
            "verdicts": list(self.verdicts),
        }
