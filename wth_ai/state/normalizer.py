"""Normalisasi hasil request menjadi `Observation` ringkas (state-friendly).

GAP-010/state: agent tidak perlu membaca ulang seluruh body. Yang disimpan:
status, panjang, fingerprint body, header penting (nilai di-redaksi untuk cookie),
waktu, dan potongan kecil. Dua observasi bisa dibandingkan secara deterministik
tanpa LLM.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

#: Header yang relevan untuk sinyal keamanan/triage.
SALIENT_HEADERS = (
    "server",
    "x-powered-by",
    "content-type",
    "location",
    "www-authenticate",
    "content-security-policy",
    "x-frame-options",
    "access-control-allow-origin",
    "x-request-id",
)

_SNIPPET_LIMIT = 300


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def body_fingerprint(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8", "replace")).hexdigest()[:16]


def _get(source: Any, *names: str, default: Any = "") -> Any:
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return default
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return default


def _cookie_names(set_cookie: str) -> list[str]:
    names: list[str] = []
    for part in (set_cookie or "").split(","):
        head = part.split(";", 1)[0].strip()
        name = head.split("=", 1)[0].strip()
        if name and name.lower() not in {"path", "domain", "expires", "max-age", "samesite", "secure", "httponly"}:
            names.append(name)
    return names


def salient_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """Header penting saja; set-cookie disimpan sebagai NAMA cookie, bukan nilainya."""
    if not headers:
        return {}
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    out: dict[str, str] = {}
    for name in SALIENT_HEADERS:
        if name in lowered:
            out[name] = lowered[name][:200]
    if "set-cookie" in lowered:
        names = _cookie_names(lowered["set-cookie"])
        if names:
            out["set-cookie-names"] = ",".join(names)
    return out


@dataclass(frozen=True)
class Observation:
    obs_id: str
    method: str
    url: str
    status: int
    body_length: int
    body_sha: str
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    role: str = ""
    transcript_id: str = ""
    source: str = ""
    snippet: str = ""
    captured_at: str = field(default_factory=_now)

    def to_pair(self) -> dict[str, Any]:
        """Format yang dipahami verifier/pair lama."""
        return {
            "role": self.role,
            "method": self.method,
            "url": self.url,
            "response_status": self.status,
            "body_snippet": self.snippet,
            "response_headers": dict(self.headers),
            "body_length": self.body_length,
            "body_sha": self.body_sha,
            "elapsed_ms": self.elapsed_ms,
            "transcript_id": self.transcript_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "obs_id": self.obs_id,
            "method": self.method,
            "url": self.url,
            "status": self.status,
            "body_length": self.body_length,
            "body_sha": self.body_sha,
            "headers": dict(self.headers),
            "elapsed_ms": self.elapsed_ms,
            "role": self.role,
            "transcript_id": self.transcript_id,
            "source": self.source,
            "snippet": self.snippet,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Observation":
        return cls(
            obs_id=str(data.get("obs_id") or ""),
            method=str(data.get("method") or "GET").upper(),
            url=str(data.get("url") or ""),
            status=int(data.get("status") or 0),
            body_length=int(data.get("body_length") or 0),
            body_sha=str(data.get("body_sha") or ""),
            headers=dict(data.get("headers") or {}),
            elapsed_ms=float(data.get("elapsed_ms") or 0.0),
            role=str(data.get("role") or ""),
            transcript_id=str(data.get("transcript_id") or ""),
            source=str(data.get("source") or ""),
            snippet=str(data.get("snippet") or ""),
            captured_at=str(data.get("captured_at") or _now()),
        )


def observation_from_result(
    result: Any,
    *,
    role: str = "",
    source: str = "http_probe",
) -> Observation:
    """Bangun Observation dari TerminalResult/ExecutionResult/dict pair."""
    body = str(_get(result, "stdout", "body", "body_snippet", default="") or "")
    headers = _get(result, "response_headers", "headers", default={}) or {}
    if not isinstance(headers, Mapping):
        headers = {}
    transcript_id = str(_get(result, "transcript_id", default="") or "")
    method = str(_get(result, "method", default="") or "GET").upper()
    url = str(_get(result, "url", default="") or _get(result, "command", default="") or "")
    status = int(_get(result, "status_code", "response_status", "status", default=0) or 0)
    elapsed = float(_get(result, "elapsed_ms", default=0.0) or 0.0)
    obs_id = transcript_id or f"OBS-{body_fingerprint(url + body)[:10]}"
    return Observation(
        obs_id=obs_id,
        method=method,
        url=url,
        status=status,
        body_length=len(body),
        body_sha=body_fingerprint(body),
        headers=salient_headers(headers),
        elapsed_ms=elapsed,
        role=role,
        transcript_id=transcript_id,
        source=source,
        snippet=body[:_SNIPPET_LIMIT],
    )


def observation_from_pair(
    pair: Mapping[str, Any], *, role: str = "", source: str = "pair"
) -> Observation:
    return observation_from_result(pair, role=role or str(pair.get("role") or ""), source=source)


def diff_observations(
    baseline: Observation,
    probe: Observation,
    *,
    length_delta_ratio: float = 0.20,
) -> list[str]:
    """Daftar perubahan nyata antara dua observasi (deterministik, tanpa LLM)."""
    changes: list[str] = []
    if baseline.status != probe.status:
        changes.append(f"status {baseline.status} -> {probe.status}")
    if baseline.body_sha != probe.body_sha:
        changes.append("body fingerprint berbeda")
    if baseline.body_length and probe.body_length:
        delta = abs(probe.body_length - baseline.body_length) / max(baseline.body_length, 1)
        if delta >= length_delta_ratio:
            changes.append(f"panjang {baseline.body_length} -> {probe.body_length} ({delta:.0%})")
    for name in ("server", "x-powered-by", "content-type", "location", "www-authenticate",
                 "content-security-policy", "x-frame-options", "access-control-allow-origin"):
        before = baseline.headers.get(name, "")
        after = probe.headers.get(name, "")
        if before != after:
            changes.append(f"header {name}: {before or '(tidak ada)'} -> {after or '(tidak ada)'}")
    if baseline.headers.get("set-cookie-names", "") != probe.headers.get("set-cookie-names", ""):
        changes.append(
            f"cookie names: {baseline.headers.get('set-cookie-names', '(tidak ada)')} "
            f"-> {probe.headers.get('set-cookie-names', '(tidak ada)')}"
        )
    if baseline.elapsed_ms and probe.elapsed_ms and probe.elapsed_ms >= baseline.elapsed_ms * 3:
        changes.append(f"waktu {baseline.elapsed_ms:.0f}ms -> {probe.elapsed_ms:.0f}ms (>=3x)")
    return changes
