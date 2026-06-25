"""Response analysis engine (Fase 6, flowchart Stage 7).

Compares baseline vs variant responses, detects anomalies (status/header/body
/redirect/timing/access-control diffs, verbose errors, state changes), captures
optional screenshots, and emits additional finding candidates with evidence.
Pure analysis — issues no destructive requests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Verbose-error / debug signatures (information disclosure clue).
_ERROR_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("stack_trace", re.compile(r"(?i)traceback \(most recent call last\)|at [\w.$]+\([\w.]+:\d+\)")),
    ("sql_error", re.compile(r"(?i)sql syntax|sqlstate|psql:|mysql_fetch|ORA-\d{5}|unclosed quotation")),
    ("framework_debug", re.compile(r"(?i)whoops\\|laravel|django.*debug|werkzeug debugger|symfony.*exception")),
    ("dotnet_error", re.compile(r"(?i)server error in '/' application|system\.\w+exception")),
    ("php_error", re.compile(r"(?i)<b>(warning|fatal error|notice)</b>:")),
)


def detect_verbose_error(body: str) -> str | None:
    """Return the error signature type if the body leaks verbose errors."""
    for label, pat in _ERROR_SIGNATURES:
        if pat.search(body or ""):
            return label
    return None


@dataclass
class ResponseDiff:
    status_changed: bool
    length_delta: int
    header_diff: list[str]
    redirect_change: bool
    timing_delta_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_changed": self.status_changed,
            "length_delta": self.length_delta,
            "header_diff": self.header_diff,
            "redirect_change": self.redirect_change,
            "timing_delta_ms": self.timing_delta_ms,
        }


class ResponseAnalyzer:
    def compare(self, baseline: dict[str, Any], variant: dict[str, Any]) -> ResponseDiff:
        b_status = baseline.get("status", 0)
        v_status = variant.get("status", 0)
        b_len = int(baseline.get("length", baseline.get("len", 0)) or 0)
        v_len = int(variant.get("length", variant.get("len", 0)) or 0)
        b_hdr = {k.lower() for k in (baseline.get("headers", {}) or {})}
        v_hdr = {k.lower() for k in (variant.get("headers", {}) or {})}
        diff = sorted(b_hdr.symmetric_difference(v_hdr))
        return ResponseDiff(
            status_changed=b_status != v_status,
            length_delta=v_len - b_len,
            header_diff=diff,
            redirect_change=(b_status in (301, 302)) != (v_status in (301, 302)),
            timing_delta_ms=int(variant.get("elapsed_ms", 0)) - int(baseline.get("elapsed_ms", 0)),
        )


def analyze_exec_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan execution observations for anomalies and emit finding candidates."""
    findings: list[dict[str, Any]] = []
    for r in results:
        obs = r.get("observation", {}) or {}
        # verbose error disclosure — only present if a module captured body text;
        # exec modules store fingerprints, so we rely on a "body_excerpt" if present.
        excerpt = obs.get("body_excerpt", "")
        sig = detect_verbose_error(excerpt) if excerpt else None
        if sig:
            findings.append({
                "title": f"HIPOTESIS: Verbose error disclosure ({sig})",
                "asset": r.get("target_url", ""),
                "category": "info_disclosure", "severity_hint": "medium", "severity": "medium",
                "detail": f"Response leaks {sig} indicators (information disclosure).",
                "evidence": {"signature": sig, "module": r.get("module")},
                "confidence": 0.55, "proven": False,
            })
        resp = obs.get("response", {}) or {}
        if isinstance(resp, dict) and isinstance(resp.get("status"), int) and resp["status"] >= 500:
            findings.append({
                "title": "HIPOTESIS: Server error anomaly", "asset": r.get("target_url", ""),
                "category": "anomaly", "severity_hint": "low", "severity": "low",
                "detail": f"Observed HTTP {resp['status']} during safe probing.",
                "evidence": {"status": resp["status"], "module": r.get("module")},
                "confidence": 0.4, "proven": False,
            })
    return findings


def capture_screenshot(url: str, out_path: Any) -> str | None:
    """Best-effort headless screenshot via nodriver. Returns path or None.

    Disabled unless ``WTH_SCREENSHOTS=1`` (heavy/optional). Never raises.
    """
    import os

    if os.environ.get("WTH_SCREENSHOTS") != "1":
        return None
    try:
        import asyncio

        import nodriver as uc

        async def _shot() -> bool:
            browser = await uc.start(headless=True, sandbox=False)
            page = await browser.get(url)
            await page.save_screenshot(str(out_path))
            await browser.stop()
            return True

        ok = asyncio.run(_shot())
        return str(out_path) if ok else None
    except Exception:  # noqa: BLE001 — screenshots are best-effort
        return None
