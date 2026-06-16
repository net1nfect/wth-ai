"""Response-Diff Brain — with optional authenticated session."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from wth_ai.models import AttackSurface, ParamCandidate, Scope


@dataclass
class ProbeResult:
    url: str
    param: str
    mutation: str
    anomaly_score: float
    signal: str
    baseline_hash: str = ""
    mutated_hash: str = ""
    status_delta: int = 0
    length_delta: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "param": self.param,
            "mutation": self.mutation,
            "anomaly_score": self.anomaly_score,
            "signal": self.signal,
            "status_delta": self.status_delta,
            "length_delta": self.length_delta,
        }


class ResponseDiffBrain:
    """Baseline + mutation probing on param URLs from recon."""

    MUTATIONS = {
        "idor_id_swap": lambda v: "999999" if v.isdigit() else "1",
        "xss_probe": lambda v: f"{v}<wthprobe>",
        "sqli_probe": lambda v: f"{v}'",
        "ssrf_probe": lambda v: "http://127.0.0.1/",
    }

    def __init__(self, *, timeout: int = 10, rate_limit: float = 2.0, scope: Scope | None = None) -> None:
        self.timeout = timeout
        self.rate_limit = rate_limit
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "WTH-AI-DiffBrain/5.1"})
        self._session_engine = None
        if scope and scope.test_accounts:
            from wth_ai.http.session_engine import SessionEngine
            self._session_engine = SessionEngine(scope, rate_limit_rps=rate_limit)

    def _fetch(self, url: str, role: str | None = None) -> tuple[int, str, str]:
        try:
            if self._session_engine and role:
                resp = self._session_engine.request(role, "GET", url, timeout=self.timeout)
                body = (resp.get("body") or "")[:8000]
                status = resp.get("status", 0)
            else:
                r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                body = r.text[:8000]
                status = r.status_code
            return status, body, hashlib.md5(body.encode()).hexdigest()
        except requests.RequestException:
            return 0, "", ""

    def _mutate_url(self, url: str, param: str, mutator) -> str:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if param not in qs:
            return url
        original = qs[param][0]
        qs[param] = [mutator(original)]
        new_query = urlencode(qs, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def probe_param(self, candidate: ParamCandidate, *, role: str | None = None) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        base_status, base_body, base_hash = self._fetch(candidate.url, role)
        if base_status == 0:
            return results

        for mut_name, mutator in self.MUTATIONS.items():
            mutated_url = self._mutate_url(candidate.url, candidate.param, mutator)
            if mutated_url == candidate.url:
                continue
            mut_status, mut_body, mut_hash = self._fetch(mutated_url, role)
            if mut_status == 0:
                continue

            score = 0.0
            signal = ""
            status_delta = abs(mut_status - base_status)
            length_delta = abs(len(mut_body) - len(base_body))

            if status_delta >= 1:
                score += 0.3
                signal = "status_change"
            if length_delta > 200:
                score += 0.2
                signal = signal or "length_change"
            if mut_name == "xss_probe" and "<wthprobe>" in mut_body:
                score += 0.8
                signal = "xss_reflection"
            if mut_name == "idor_id_swap" and mut_hash != base_hash and mut_status == 200:
                score += 0.5
                signal = "idor_diff"
            if mut_name == "sqli_probe" and re.search(r"sql|syntax|mysql|postgres|oracle", mut_body, re.I):
                score += 0.6
                signal = "sqli_error"

            if score >= 0.3:
                results.append(ProbeResult(
                    url=candidate.url,
                    param=candidate.param,
                    mutation=mut_name,
                    anomaly_score=round(score, 2),
                    signal=signal,
                    baseline_hash=base_hash,
                    mutated_hash=mut_hash,
                    status_delta=status_delta,
                    length_delta=length_delta,
                ))
            time.sleep(1.0 / max(self.rate_limit, 0.1))
        return results

    def probe_surface(self, surface: AttackSurface, *, limit: int = 20, scope: Scope | None = None) -> list[dict[str, Any]]:
        """Probe param URLs and return pre_findings for planner."""
        if scope and scope.test_accounts and not self._session_engine:
            from wth_ai.http.session_engine import SessionEngine
            self._session_engine = SessionEngine(scope, rate_limit_rps=self.rate_limit)

        candidates = surface.parameters[:limit]
        if not candidates:
            for url in surface.urls[:limit]:
                if "?" in url:
                    for pair in urlparse(url).query.split("&"):
                        if "=" in pair:
                            candidates.append(ParamCandidate(url=url, param=pair.split("=", 1)[0]))
                if len(candidates) >= limit:
                    break

        role = None
        if self._session_engine and self._session_engine.configured():
            role = self._session_engine.role_names()[0]

        pre_findings: list[dict[str, Any]] = []
        for cand in candidates[:limit]:
            for probe in self.probe_param(cand, role=role):
                if probe.anomaly_score >= 0.4:
                    pre_findings.append({
                        **probe.to_dict(),
                        "title": f"{probe.signal} on {cand.param} @ {cand.url[:80]}",
                        "severity_hint": "high" if probe.anomaly_score >= 0.7 else "medium",
                        "authenticated": bool(role),
                    })
        return pre_findings
