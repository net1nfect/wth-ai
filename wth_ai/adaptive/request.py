"""A minimal HTTP request model used by the adaptive mutation engine."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class HttpRequest:
    method: str = "GET"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None

    def __post_init__(self) -> None:
        self.method = (self.method or "GET").upper()

    @classmethod
    def from_probe(cls, probe: Mapping[str, Any]) -> "HttpRequest":
        headers = probe.get("headers") or {}
        return cls(
            method=str(probe.get("method") or "GET").upper(),
            url=str(probe.get("url") or ""),
            headers={str(k): str(v) for k, v in dict(headers).items()},
            body=probe.get("body") if probe.get("body") is not None else None,
        )

    def to_probe(self, *, purpose: str = "") -> dict[str, Any]:
        probe: dict[str, Any] = {"method": self.method.upper(), "url": self.url}
        if self.headers:
            probe["headers"] = dict(self.headers)
        if self.body is not None:
            probe["body"] = self.body
        if purpose:
            probe["purpose"] = purpose
        return probe

    def fingerprint(self) -> str:
        raw = json.dumps(
            [self.method, self.url, sorted((k.lower(), v) for k, v in self.headers.items()), self.body],
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]

    def clone(self) -> "HttpRequest":
        return HttpRequest(self.method, self.url, dict(self.headers), self.body)
