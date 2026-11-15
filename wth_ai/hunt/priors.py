"""GAP-008 — prior Bayesian per kelas kerentanan (deterministik, offline).

Masalah yang diselesaikan: `propose_next_tests` lama hanya mengusulkan kelas yang
punya aturan eksplisit. Dua request pair dengan sinyal yang tidak cocok aturan →
nol usulan. Di sini sinyal (parameter, path, status, header, cuplikan body, temuan
sebelumnya) diubah menjadi fitur, lalu posterior kelas dihitung dengan naive Bayes.

Kejujuran angka: `DEFAULT_PRIORS` dan `LIKELIHOODS` adalah **konstanta heuristik**
(alat urutan kerja), bukan probabilitas hasil pengukuran. Bobot kelas yang benar-benar
diukur (`$WTH_HOME/eval/weights.json`, ditulis loop self-improvement) tetap dipakai
sebagai pengali prioritas. Override tersedia di `$WTH_HOME/eval/priors.json` agar
operator bisa mengoreksi tanpa mengubah kode.

Semua keluaran deterministik, tanpa jaringan, dan hanya mengusulkan probe baca /
non-destruktif (satu request, bertanda `purpose`).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

#: Prior dasar (akan dinormalisasi). Nilai = urutan kerja awal, bukan klaim.
DEFAULT_PRIORS: dict[str, float] = {
    "business_logic": 0.10,
    "idor": 0.14,
    "auth": 0.13,
    "bypass": 0.08,
    "sqli": 0.12,
    "xss": 0.11,
    "ssrf": 0.09,
    "ssti": 0.08,
    "path_traversal": 0.09,
    "cmdi": 0.05,
    "api_graphql": 0.05,
}

#: fitur -> {kelas: likelihood ratio}. LR > 1 menaikkan, < 1 menurunkan.
LIKELIHOODS: dict[str, dict[str, float]] = {
    # parameter
    "param:url": {"ssrf": 4.0},
    "param:redirect": {"ssrf": 2.5, "bypass": 1.3},
    "param:id": {"idor": 4.0, "auth": 1.2},
    "param:file": {"path_traversal": 4.0},
    "param:path": {"path_traversal": 4.0},
    "param:q": {"xss": 2.0, "sqli": 2.0, "ssti": 1.4},
    "param:cmd": {"cmdi": 5.0},
    "param:template": {"ssti": 2.5},
    # path
    "path:/admin": {"auth": 3.0, "bypass": 2.0, "idor": 1.4},
    "path:/api": {"idor": 2.0, "auth": 1.3, "api_graphql": 1.3},
    "path:/graphql": {"api_graphql": 6.0},
    "path:/login": {"auth": 3.0},
    "path:/upload": {"path_traversal": 1.6, "auth": 1.2},
    "path:/search": {"xss": 1.5, "sqli": 1.5},
    # status
    "status:401/403": {"auth": 3.0, "bypass": 3.0},
    "status:429": {"business_logic": 3.0},
    "status:5xx": {"sqli": 1.6, "ssti": 1.4, "cmdi": 1.3},
    # body
    "body:sql-error": {"sqli": 6.0},
    "body:template-error": {"ssti": 5.0},
    "body:passwd": {"path_traversal": 6.0},
    "body:uid": {"cmdi": 6.0},
    "body:graphql": {"api_graphql": 3.0},
    "body:script": {"xss": 1.6},
    # header/teknologi
    "header:php": {"sqli": 1.5, "path_traversal": 1.2},
    "header:express": {"api_graphql": 1.3},
    "header:flask": {"ssti": 1.5},
    "header:json": {"api_graphql": 1.3, "idor": 1.2},
    # lintas-peran (user_a vs user_b) = sinyal objek
    "role:cross-user": {"idor": 1.8},
}

#: Kelas tetangga yang ikut naik saat sebuah temuan sudah ada.
NEIGHBOURS: dict[str, tuple[str, ...]] = {
    "idor": ("auth",),
    "auth": ("bypass", "idor"),
    "ssrf": ("auth",),
    "xss": ("ssti",),
    "sqli": ("cmdi",),
    "path_traversal": ("cmdi",),
    "api_graphql": ("idor", "auth"),
}

_PARAM_ALIASES: dict[str, str] = {
    "url": "url", "uri": "url", "link": "url", "dest": "url", "target": "url",
    "callback": "url", "image": "url", "fetch": "url", "proxy": "url",
    "redirect": "redirect", "next": "redirect", "return": "redirect", "returnurl": "redirect",
    "id": "id", "uid": "id", "user": "id", "user_id": "id", "userid": "id",
    "account": "id", "account_id": "id", "uuid": "id", "order": "id", "order_id": "id",
    "doc": "id", "document": "id", "file_id": "id", "fileid": "id",
    "file": "file", "filename": "file", "download": "file", "attachment": "file",
    "path": "path", "folder": "path", "dir": "path", "directory": "path",
    "q": "q", "query": "q", "search": "q", "s": "q", "keyword": "q", "term": "q",
    "cmd": "cmd", "command": "cmd", "exec": "cmd", "execute": "cmd",
    "ping": "cmd", "host": "cmd", "ip": "cmd",
    "template": "template", "view": "template", "page": "template", "name": "template",
}

_BODY_CUES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("you have an error in your sql syntax", "sql syntax", "sqlite error",
      "unclosed quotation mark", "pg::syntaxerror", "ora-0"), "body:sql-error"),
    (("jinja2", "jinja", "twig", "freemarker", "velocity", "thymeleaf",
      "template error", "templateerror"), "body:template-error"),
    (("root:x:0:0", "root:*:0:0"), "body:passwd"),
    (("uid=", "gid="), "body:uid"),
    (("__typename", "graphql"), "body:graphql"),
    (("<script", "onerror=", "alert("), "body:script"),
)


@dataclass(frozen=True)
class ClassBelief:
    """Posterior satu kelas + fitur yang menggerakkannya (untuk alasan usulan)."""

    vuln_class: str
    probability: float
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "vuln_class": self.vuln_class,
            "probability": round(self.probability, 4),
            "evidence": list(self.evidence),
        }


# --------------------------------------------------------------------------- #
# konfigurasi
# --------------------------------------------------------------------------- #
def _config_path() -> Path | None:
    try:
        from wth_ai.workspace import wth_home

        return wth_home() / "eval" / "priors.json"
    except Exception:  # noqa: BLE001
        return None


def load_prior_config(path: Path | None = None) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Prior + likelihood dari konstanta, ditimpa `eval/priors.json` bila ada."""
    priors = dict(DEFAULT_PRIORS)
    likelihoods = {k: dict(v) for k, v in LIKELIHOODS.items()}
    target = path or _config_path()
    if target is None or not Path(target).exists():
        return _normalize(priors), likelihoods
    try:
        raw = json.loads(Path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _normalize(priors), likelihoods
    for key, value in (raw.get("priors") or {}).items():
        try:
            priors[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    for feature, table in (raw.get("likelihoods") or {}).items():
        if not isinstance(table, Mapping):
            continue
        merged = likelihoods.setdefault(str(feature), {})
        for cls, lr in table.items():
            try:
                merged[str(cls)] = float(lr)
            except (TypeError, ValueError):
                continue
    return _normalize(priors), likelihoods


def _normalize(priors: Mapping[str, float]) -> dict[str, float]:
    positive = {k: max(0.0, float(v)) for k, v in priors.items()}
    total = sum(positive.values())
    if total <= 0:
        return dict(DEFAULT_PRIORS)
    return {k: v / total for k, v in positive.items() if v > 0}


# --------------------------------------------------------------------------- #
# ekstraksi fitur
# --------------------------------------------------------------------------- #
def _url_of(item: Mapping[str, Any]) -> str:
    return str(item.get("target") or item.get("url") or "")


def _parse(url: str):
    return urlparse(url if "://" in url else f"https://{url}")


def _query(url: str) -> dict[str, str]:
    return {k.lower(): (v[0] if v else "") for k, v in parse_qs(_parse(url).query).items()}


def _param_feature(name: str) -> str | None:
    alias = _PARAM_ALIASES.get(name.lower())
    return f"param:{alias}" if alias else None


def extract_features(
    findings: Iterable[Mapping[str, Any]] = (),
    request_pairs: Iterable[Mapping[str, Any]] = (),
) -> dict[str, list[str]]:
    """Fitur unik -> daftar bukti singkat. Satu fitur dihitung sekali (LR sekali)."""
    features: dict[str, list[str]] = {}

    def add(name: str, evidence: str) -> None:
        features.setdefault(name, []).append(evidence)

    for pair in request_pairs:
        if not isinstance(pair, Mapping):
            continue
        url = _url_of(pair)
        parsed = _parse(url)
        path = parsed.path.lower()
        for key in _query(url):
            feature = _param_feature(key)
            if feature:
                add(feature, f"param '{key}' @ {url[:90]}")
        for token in ("admin", "api", "graphql", "login", "upload", "search"):
            if f"/{token}" in path:
                add(f"path:/{token}", f"path {path[:70]}")
        status = int(pair.get("response_status") or 0)
        if status in (401, 403):
            add("status:401/403", f"status {status} @ {url[:90]}")
        elif status == 429:
            add("status:429", f"status 429 @ {url[:90]}")
        elif status >= 500:
            add("status:5xx", f"status {status} @ {url[:90]}")
        snippet = str(pair.get("body_snippet") or "").lower()
        for cues, feature in _BODY_CUES:
            if any(cue in snippet for cue in cues):
                add(feature, f"body memuat '{next(c for c in cues if c in snippet)}'")
        headers = pair.get("response_headers")
        if isinstance(headers, Mapping):
            flat = {str(k).lower(): str(v).lower() for k, v in headers.items()}
            powered = flat.get("x-powered-by", "")
            if "php" in powered:
                add("header:php", "x-powered-by: php")
            if "express" in powered:
                add("header:express", "x-powered-by: express")
            if "werkzeug" in flat.get("server", "") or "flask" in powered:
                add("header:flask", "server: werkzeug/flask")
            if "application/json" in flat.get("content-type", ""):
                add("header:json", "content-type: application/json")
        if str(pair.get("role") or "") in {"user_a", "user_b"}:
            add("role:cross-user", f"peran {pair.get('role')} pada URL yang sama")

    for finding in findings:
        if not isinstance(finding, Mapping):
            finding = getattr(finding, "__dict__", {}) or {}
        url = _url_of(finding)
        if url:
            # URL temuan juga membawa sinyal (param/path), walau tanpa pair.
            for key in _query(url):
                feature = _param_feature(key)
                if feature:
                    add(feature, f"param '{key}' @ temuan {url[:90]}")
            path = _parse(url).path.lower()
            for token in ("admin", "api", "graphql", "login", "upload", "search"):
                if f"/{token}" in path:
                    add(f"path:/{token}", f"path temuan {path[:70]}")
        vuln_class = str(finding.get("vuln_class") or finding.get("type") or "").lower()
        if vuln_class:
            add(f"finding:{vuln_class}", f"temuan kelas {vuln_class}")
    return features


# --------------------------------------------------------------------------- #
# posterior
# --------------------------------------------------------------------------- #
def _finding_likelihoods(feature: str) -> dict[str, float]:
    vuln_class = feature.split(":", 1)[1]
    table: dict[str, float] = {vuln_class: 6.0}
    for neighbour in NEIGHBOURS.get(vuln_class, ()):
        table[neighbour] = max(table.get(neighbour, 1.0), 1.4)
    return table


def class_posterior(
    features: Mapping[str, Sequence[str]],
    *,
    priors: Mapping[str, float] | None = None,
    likelihoods: Mapping[str, Mapping[str, float]] | None = None,
) -> list[ClassBelief]:
    """Posterior naive Bayes, dinormalisasi (jumlah = 1), urut menurun."""
    resolved_priors, resolved_likelihoods = load_prior_config()
    prior_map = _normalize(priors) if priors else resolved_priors
    lr_map = dict(resolved_likelihoods)
    if likelihoods:
        for feature, table in likelihoods.items():
            lr_map[feature] = dict(table)

    logp = {cls: math.log(max(prob, 1e-9)) for cls, prob in prior_map.items()}
    driven: dict[str, list[str]] = {}
    for feature in features:
        if feature.startswith("finding:"):
            table = lr_map.get(feature) or _finding_likelihoods(feature)
        else:
            table = lr_map.get(feature)
        if not table:
            continue
        for cls, lr in table.items():
            if cls not in logp:
                continue
            ratio = min(max(float(lr), 0.05), 20.0)
            logp[cls] += math.log(ratio)
            driven.setdefault(cls, []).append(f"{feature} ×{ratio:g}")

    top = max(logp.values())
    weights = {cls: math.exp(value - top) for cls, value in logp.items()}
    total = sum(weights.values())
    beliefs = [
        ClassBelief(cls, weight / total, tuple(driven.get(cls, ())[:4]))
        for cls, weight in weights.items()
    ]
    beliefs.sort(key=lambda belief: (-belief.probability, belief.vuln_class))
    return beliefs


# --------------------------------------------------------------------------- #
# usulan probe
# --------------------------------------------------------------------------- #
def _url_score(item: Mapping[str, Any], url: str) -> int:
    score = len(_query(url)) * 2
    path = _parse(url).path.lower()
    score += sum(1 for token in ("admin", "api", "graphql", "login", "upload", "search") if f"/{token}" in path)
    try:
        status = int(item.get("response_status") or 0)
    except (TypeError, ValueError):
        status = 0
    if status >= 400:
        score += 1
    return score


def _best_url(findings: Iterable[Mapping[str, Any]], pairs: Iterable[Mapping[str, Any]]) -> str:
    """URL dengan sinyal terbanyak (param/path/status), fallback URL pertama."""
    best_url, best_score = "", -1
    for item in list(pairs) + list(findings):
        url = _url_of(item)
        if not url:
            continue
        score = _url_score(item, url)
        if score > best_score:
            best_url, best_score = url, score
    return best_url


def _with_query(url: str, changes: Mapping[str, str]) -> str:
    parsed = _parse(url)
    query = {k: v for k, v in parse_qs(parsed.query).items()}
    for key, value in changes.items():
        query[key] = [value]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _pick_param(url: str, preferred: Sequence[str]) -> str | None:
    query = _query(url)
    for name in preferred:
        for key in query:
            if _PARAM_ALIASES.get(key) == name:
                return key
    return next(iter(query), None)


def _swap_last_id(url: str) -> str:
    parsed = _parse(url)
    matches = list(re.finditer(r"/(\d+)(?=/|$)", parsed.path))
    if not matches:
        return url
    last = matches[-1]
    replacement = str(int(last.group(1)) + 1)
    path = parsed.path[: last.start(1)] + replacement + parsed.path[last.end(1):]
    return urlunparse(parsed._replace(path=path))


def _proposal_probe(vuln_class: str, url: str) -> dict[str, Any] | None:
    """Probe baca/non-destruktif untuk satu kelas; None bila tidak bisa konkret."""
    low = url.lower()
    if vuln_class == "idor":
        swapped = _swap_last_id(url)
        if swapped == url:
            key = _pick_param(url, ("id",))
            if key is None:
                return None
            query = _query(url)
            current = query.get(key, "1")
            digit = re.sub(r"\D", "", current or "1") or "1"
            swapped = _with_query(url, {key: str(int(digit) + 1)})
        return {"method": "GET", "url": swapped, "purpose": "bayesian:idor neighbour object"}
    if vuln_class == "auth":
        return {
            "method": "GET", "url": url,
            "headers": {"Authorization": "Bearer wth-invalid-9z8y7"},
            "purpose": "bayesian:auth boundary (token invalid)",
        }
    if vuln_class == "sqli":
        key = _pick_param(url, ("q", "id", "template"))
        if key is None:
            return None
        current = _query(url).get(key, "") or "1"
        return {
            "method": "GET", "url": _with_query(url, {key: f"{current}'"}),
            "purpose": "bayesian:sqli quote (read-only)",
        }
    if vuln_class == "xss":
        key = _pick_param(url, ("q", "template"))
        if key is None:
            return None
        return {
            "method": "GET", "url": _with_query(url, {key: "<wthXSSprobe9z8y7>"}),
            "purpose": "bayesian:xss reflection marker",
        }
    if vuln_class == "ssrf":
        key = _pick_param(url, ("url", "redirect"))
        if key is None:
            return None
        return {
            "method": "GET", "url": _with_query(url, {key: "http://127.0.0.1:1/wth-ssrf-probe"}),
            "purpose": "bayesian:ssrf self-probe",
        }
    if vuln_class == "ssti":
        key = _pick_param(url, ("template", "q"))
        if key is None:
            return None
        return {
            "method": "GET", "url": _with_query(url, {key: "{{7*7}}wth9z8y7"}),
            "purpose": "bayesian:ssti computed expression",
        }
    if vuln_class == "cmdi":
        key = _pick_param(url, ("cmd",))
        if key is None:
            return None
        current = _query(url).get(key, "") or "1"
        return {
            "method": "GET", "url": _with_query(url, {key: f"{current};id"}),
            "purpose": "bayesian:cmdi uid probe",
        }
    if vuln_class == "path_traversal":
        key = _pick_param(url, ("file", "path"))
        if key is None:
            return None
        return {
            "method": "GET", "url": _with_query(url, {key: "../../../../etc/passwd"}),
            "purpose": "bayesian:path traversal read-only",
        }
    if vuln_class == "api_graphql":
        if "graphql" not in low:
            return None
        return {
            "method": "GET", "url": _with_query(url, {"query": "{__typename}"}),
            "purpose": "bayesian:graphql typename probe",
        }
    if vuln_class == "bypass":
        variant = url if url.endswith("/") else url + "/"
        return {"method": "GET", "url": variant, "purpose": "bayesian:routing variant"}
    if vuln_class == "business_logic":
        return {
            "method": "GET", "url": url,
            "headers": {"X-WTH-Probe": "rate-limit-window"},
            "purpose": "bayesian:rate-limit window measure",
        }
    return None


def bayesian_next_tests(
    *,
    findings: Iterable[Mapping[str, Any]] = (),
    request_pairs: Iterable[Mapping[str, Any]] = (),
    weights: Mapping[str, float] | None = None,
    limit: int = 4,
    min_probability: float = 0.05,
    priors: Mapping[str, float] | None = None,
    likelihoods: Mapping[str, Mapping[str, float]] | None = None,
) -> list[Any]:
    """Usulan `NextTest` dari posterior kelas. Prioritas < aturan temuan langsung.

    Prioritas dijaga di kisaran 0,7–1,2 supaya usulan berbasis sinyal tidak
    mendahului aturan yang sudah punya temuan konkret (≥ ~1,1–1,4).
    """
    from wth_ai.hunt.proactive import NextTest

    finding_list = [f if isinstance(f, Mapping) else getattr(f, "__dict__", {}) or {} for f in findings]
    pair_list = [p for p in request_pairs if isinstance(p, Mapping)]
    features = extract_features(finding_list, pair_list)
    url = _best_url(finding_list, pair_list)
    if not features or not url:
        return []
    beliefs = class_posterior(features, priors=priors, likelihoods=likelihoods)

    proposals: list[NextTest] = []
    for belief in beliefs:
        if belief.probability < min_probability or len(proposals) >= max(1, limit):
            break
        probe = _proposal_probe(belief.vuln_class, url)
        if probe is None:
            continue
        weight = float((weights or {}).get(belief.vuln_class, 1.0))
        rationale = (
            f"Posterior {belief.probability:.2f} untuk {belief.vuln_class} "
            f"(prior heuristik, bukan klaim). Penggerak: "
            + ("; ".join(belief.evidence) if belief.evidence else "prior saja")
        )
        proposals.append(NextTest(
            title=f"Uji {belief.vuln_class} (posterior {belief.probability:.2f}) pada {url[:60]}",
            rationale=rationale,
            probe=probe,
            risk="low",
            priority=round((0.7 + belief.probability * 0.5) * max(weight, 0.1), 3),
            vuln_class=belief.vuln_class,
        ))
    return proposals
