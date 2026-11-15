"""Verifikasi deterministik berbasis pasangan observasi (baseline vs probe).

Tujuan: verdict `verified`/`false_positive` hanya keluar jika ada sinyal konkret.
Tidak ada sinyal != tidak ada vuln; kasus tanpa sinyal menjadi `needs_more_evidence`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

SQL_ERROR_RE = re.compile(
    r"(you have an error in your sql syntax|sql syntax.*error|mysql_fetch|"
    r"sqlite(?:_| )error|postgresql.*error|pg::syntaxerror|ora-\d{4,}|"
    r"unclosed quotation mark|syntax error at or near|quoted string not properly terminated)",
    re.IGNORECASE,
)
CMDI_RE = re.compile(r"uid=\d+\([^)]*\)\s+gid=\d+\(", re.IGNORECASE)
PASSWD_RE = re.compile(r"root:x:0:0:root:", re.IGNORECASE)
XSS_RAW = ("<script", "<wthxssprobe", "onerror=wth", "javascript:wth")
XSS_ENCODED = ("&lt;script", "&lt;wthxssprobe", "&#60;script", "%3cscript", "\\u003cscript")

_CLASS_ALIASES = {
    "sql": "sqli",
    "sql_injection": "sqli",
    "ssti": "ssti",
    "template": "ssti",
    "cmdi": "cmdi",
    "command_injection": "cmdi",
    "command": "cmdi",
    "path_traversal": "path_traversal",
    "traversal": "path_traversal",
    "lfi": "path_traversal",
    "file_path": "path_traversal",
    "idor": "idor",
    "bola": "idor",
    "ssrf": "ssrf",
    "xss": "xss",
    "auth": "auth",
    "auth_jwt": "auth",
    "jwt": "auth",
    "bypass": "bypass",
    "open_redirect": "open_redirect",
    "redirect": "open_redirect",
    "race": "race",
    "graphql": "graphql",
    "api_graphql": "graphql",
    "business_logic": "business_logic",
}

_CLASS_KEYWORDS = (
    ("idor", ("idor", "bola", "cross-user", "cross user", "object reference")),
    ("sqli", ("sqli", "sql injection", "sql error", "sql syntax")),
    ("ssti", ("ssti", "template injection", "template")),
    ("cmdi", ("cmdi", "command injection", "rce", "uid=")),
    ("path_traversal", ("traversal", "lfi", "path traversal", "passwd")),
    ("ssrf", ("ssrf", "server-side request")),
    ("xss", ("xss", "cross-site scripting", "reflection")),
    ("open_redirect", ("open redirect", "redirect")),
    ("graphql", ("graphql",)),
    ("race", ("race condition", "race")),
    ("auth", ("auth", "jwt", "access control", "authorization", "privilege")),
)


@dataclass
class Verdict:
    verdict: str
    confidence: float
    vuln_class: str
    reasons: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "confidence": round(self.confidence, 2),
            "vuln_class": self.vuln_class,
            "reasons": list(self.reasons),
            "evidence_refs": list(self.evidence_refs),
        }


def _value(pair: Any, *names: str, default: Any = "") -> Any:
    if isinstance(pair, Mapping):
        for name in names:
            if name in pair:
                return pair[name]
        return default
    for name in names:
        if hasattr(pair, name):
            return getattr(pair, name)
    return default


def _status(pair: Any) -> int:
    try:
        return int(_value(pair, "response_status", "status", "status_code", default=0) or 0)
    except (TypeError, ValueError):
        return 0


def _body(pair: Any) -> str:
    return str(_value(pair, "body_snippet", "body", "stdout", default="") or "")


def _headers(pair: Any) -> dict[str, str]:
    headers = _value(pair, "response_headers", "headers", default={}) or {}
    if not isinstance(headers, Mapping):
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _elapsed(pair: Any) -> float:
    try:
        return float(_value(pair, "elapsed_ms", default=0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ref(pair: Any) -> str:
    return str(_value(pair, "transcript_id", "obs_id", "id", default="") or "")


def normalize_class(vuln_class: str | None, *texts: str) -> str:
    if vuln_class:
        key = vuln_class.strip().lower().replace("-", "_").replace(" ", "_")
        return _CLASS_ALIASES.get(key, key)
    haystack = " ".join(texts).lower()
    for klass, keywords in _CLASS_KEYWORDS:
        if any(word in haystack for word in keywords):
            return klass
    return "generic"


def _json_fields(body: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _diff_json_fields(before: str, after: str) -> list[str]:
    a = _json_fields(before)
    b = _json_fields(after)
    if not a or not b:
        return []
    changed = [
        key for key in ("id", "email", "role", "sub", "username", "user", "account")
        if key in a and key in b and str(a[key]) != str(b[key])
    ]
    return changed


def verify_pair(
    vuln_class: str | None,
    baseline: Any,
    probe: Any,
    *,
    expected: Sequence[str] = (),
    finding_text: str = "",
) -> Verdict:
    klass = normalize_class(vuln_class, finding_text)
    base_status, probe_status = _status(baseline), _status(probe)
    base_body, probe_body = _body(baseline), _body(probe)
    base_low, probe_low = base_body.lower(), probe_body.lower()
    refs = [r for r in (_ref(baseline), _ref(probe)) if r]
    new_markers = [
        marker for marker in expected
        if marker and marker.lower() in probe_low and marker.lower() not in base_low
    ]

    def out(verdict: str, confidence: float, reasons: Iterable[str]) -> Verdict:
        return Verdict(verdict, confidence, klass, list(reasons), refs)

    if klass == "xss":
        if any(token in probe_low for token in XSS_RAW) and not any(token in base_low for token in XSS_RAW):
            return out("verified", 0.9, ["marker XSS muncul mentah di respons probe, tidak ada di baseline"])
        if any(token in probe_low for token in XSS_ENCODED) and not any(token in base_low for token in XSS_ENCODED):
            return out("false_positive", 0.8, ["payload ter-encode di respons; tidak ada eksekusi mentah"])
        if new_markers:
            return out("verified", 0.6, [f"marker ekspektasi ditemukan: {', '.join(new_markers)}"])
        return out("needs_more_evidence", 0.3, ["tidak ada refleksi mentah yang bisa diverifikasi"])

    if klass == "sqli":
        if SQL_ERROR_RE.search(probe_body) and not SQL_ERROR_RE.search(base_body):
            return out("verified", 0.92, ["error SQL khas muncul di probe, tidak di baseline"])
        base_ms, probe_ms = _elapsed(baseline), _elapsed(probe)
        if probe_ms >= 2000 and (base_ms == 0 or base_ms < 1000) and probe_status == base_status:
            return out("verified", 0.75, [f"delai waktu {base_ms:.0f}ms -> {probe_ms:.0f}ms pada status sama"])
        if probe_low == base_low and probe_status == base_status:
            return out("needs_more_evidence", 0.2, ["respons identik; payload mungkin tidak sampai ke query"])
        return out("needs_more_evidence", 0.3, ["belum ada error/waktu yang membedakan query"])

    if klass == "ssti":
        if new_markers:
            return out("verified", 0.9, [f"hasil evaluasi template muncul: {', '.join(new_markers)}"])
        if "{{" in probe_body and "{{" not in base_body:
            return out("false_positive", 0.75, ["ekspresi template dipantulkan mentah (tidak dievaluasi)"])
        return out("needs_more_evidence", 0.3, ["tidak ada hasil evaluasi yang diharapkan"])

    if klass == "cmdi":
        if CMDI_RE.search(probe_body) and not CMDI_RE.search(base_body):
            return out("verified", 0.92, ["output uid/gid muncul di probe (command execution)"])
        if new_markers:
            return out("verified", 0.8, [f"marker command ditemukan: {', '.join(new_markers)}"])
        return out("needs_more_evidence", 0.3, ["tidak ada output command yang terverifikasi"])

    if klass == "path_traversal":
        if PASSWD_RE.search(probe_body) and not PASSWD_RE.search(base_body):
            return out("verified", 0.92, ["isi /etc/passwd terbaca di probe"])
        if new_markers:
            return out("verified", 0.8, [f"marker file ditemukan: {', '.join(new_markers)}"])
        return out("needs_more_evidence", 0.3, ["tidak ada konten file yang terbaca"])

    if klass == "ssrf":
        if new_markers:
            return out("verified", 0.85, [f"konten target internal muncul: {', '.join(new_markers)}"])
        if probe_status == 200 and base_status >= 400 and probe_body != base_body:
            return out("needs_more_evidence", 0.45, ["status membaik, tapi belum ada bukti konten internal"])
        return out("needs_more_evidence", 0.25, ["belum ada konten/metadata internal yang terbukti"])

    if klass == "idor":
        changed = _diff_json_fields(base_body, probe_body)
        if changed and probe_status == base_status and base_status == 200:
            return out("verified", 0.9, [f"objek berbeda untuk identifier berbeda: {', '.join(changed)} berubah"])
        if new_markers and probe_status == base_status:
            return out("verified", 0.8, [f"data objek lain muncul: {', '.join(new_markers)}"])
        if _json_fields(base_body) and _json_fields(base_body) == _json_fields(probe_body):
            return out("false_positive", 0.75, ["respons identik; identifier tidak mengubah objek"])
        if probe_status in (401, 403):
            return out("false_positive", 0.8, ["akses object lain ditolak (otorisasi bekerja)"])
        return out("needs_more_evidence", 0.35, ["perlu bukti objek/tenant berbeda"])

    if klass in ("auth", "bypass"):
        if base_status in (401, 403) and probe_status == 200:
            return out("verified", 0.9, [f"probe berhasil melewati penolakan baseline ({base_status} -> 200)"])
        if new_markers and probe_status == 200:
            return out("verified", 0.8, [f"data terproteksi muncul: {', '.join(new_markers)}"])
        if base_status in (401, 403) and probe_status in (401, 403):
            return out("false_positive", 0.8, ["probe tetap ditolak; bypass tidak bekerja"])
        return out("needs_more_evidence", 0.3, ["belum ada perbedaan otorisasi yang jelas"])

    if klass == "open_redirect":
        probe_location = _headers(probe).get("location", "")
        base_location = _headers(baseline).get("location", "")
        if probe_status in (301, 302, 303, 307, 308) and probe_location.startswith(("http://", "https://", "//")):
            if probe_location != base_location:
                return out("verified", 0.85, [f"Location eksternal: {probe_location[:120]}"])
        return out("needs_more_evidence", 0.3, ["tidak ada redirect eksternal yang terverifikasi"])

    if klass == "race":
        if probe_status == 200 and base_status == 409:
            return out("verified", 0.8, ["probe sukses pada resource yang di baseline mengembalikan konflik"])
        return out("needs_more_evidence", 0.3, ["butuh bukti burst/duplikasi transaksi"])

    if klass == "graphql":
        if new_markers or ("__typename" in probe_body and "__typename" not in base_body):
            return out("verified", 0.7, ["field/tipe GraphQL yang tidak di baseline terekspos"])
        return out("needs_more_evidence", 0.3, ["belum ada field tersembunyi yang terbukti"])

    if new_markers:
        return out("verified", 0.6, [f"marker ekspektasi baru muncul: {', '.join(new_markers)}"])
    if probe_status == base_status and probe_body == base_body:
        return out("needs_more_evidence", 0.2, ["respons identik dengan baseline"])
    return out("needs_more_evidence", 0.35, ["perbedaan belum cukup spesifik untuk kelas ini"])


def _roles(pair: Any) -> str:
    return str(_value(pair, "role", "step", "chain_step", default="") or "").lower()


def pick_pair_roles(pairs: Sequence[Any]) -> tuple[Any, Any]:
    baseline = probe = None
    for pair in pairs:
        role = _roles(pair)
        if baseline is None and any(token in role for token in ("baseline", "control", "reference")):
            baseline = pair
        if any(token in role for token in ("probe", "mutat", "cross", "attack", "test")):
            probe = pair
    if probe is None:
        probe = pairs[-1]
    if baseline is None:
        baseline = pairs[0]
    if baseline is probe and len(pairs) > 1:
        baseline = next((p for p in pairs if p is not probe), pairs[0])
    return baseline, probe


def verify_finding(
    finding: Any,
    pairs: Sequence[Any],
    *,
    vuln_class: str | None = None,
    expected: Sequence[str] = (),
) -> dict[str, Any] | None:
    """Verdict deterministik untuk finding. Return None jika bukti < 2 observasi."""
    clean = [p for p in pairs if p is not None]
    if len(clean) < 2:
        return None
    baseline, probe = pick_pair_roles(clean)
    text = " ".join(
        str(getattr(finding, name, "") or "")
        for name in ("title", "description", "impact")
    )
    klass_hint = vuln_class or getattr(finding, "vuln_class", "") or ""
    result = verify_pair(klass_hint, baseline, probe, expected=expected, finding_text=text)
    if result.confidence >= 0.85:
        quality = "high"
    elif result.confidence >= 0.6:
        quality = "moderate"
    else:
        quality = "low"
    missing = "" if result.verdict != "needs_more_evidence" else "; ".join(result.reasons)
    return {
        "finding_id": str(getattr(finding, "id", "") or ""),
        "verdict": result.verdict,
        "reason": "; ".join(result.reasons),
        "evidence_quality": quality,
        "missing_evidence": missing,
        "severity_adjustment": "none",
        "replan_hint": (
            f"Ulangi dengan probe {result.vuln_class} yang menghasilkan sinyal konkret"
            if result.verdict == "needs_more_evidence" else ""
        ),
        "model": "deterministic",
        "confidence": round(result.confidence, 2),
        "vuln_class": result.vuln_class,
        "evidence_refs": result.evidence_refs,
        "raw": "",
    }
