"""Response signal classifier for adaptive testing.

Turns a raw HTTP result into a small set of testable facts: was it blocked,
by which WAF/CDN vendor, was it rate-limited, did auth fail, is it a soft 404?
No LLM and no network here -- deterministic signatures only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

#: Body signatures that indicate an active edge/WAF block rather than app logic.
_BODY_BLOCK = (
    "request blocked", "request rejected", "access denied", "you have been blocked",
    "web application firewall", "attention required", "checking your browser",
    "ddos protection", "bot detection", "captcha", "incident id", "support id",
    "security policy", "not acceptable", "blocked by", "rate limit exceeded",
    "too many requests", "cf-error", "cloudflare ray id", "sucuri website firewall",
)

#: Vendor fingerprints: (name, header names, body needles).
_VENDORS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("cloudflare", ("cf-ray", "cf-cache-status", "cf-mitigated"), ("cloudflare", "ray id")),
    ("akamai", ("x-akamai-transformed", "akamai-grn", "x-akamai-request-id"), ("akamai", "reference #")),
    ("imperva", ("x-iinfo", "incap_ses", "visid_incap"), ("incapsula", "incident id")),
    ("sucuri", ("x-sucuri-id", "x-sucuri-cache"), ("sucuri", "website firewall")),
    ("f5", ("x-wa-info", "bigipserver", "x-cnection"), ("the requested url was rejected", "support id")),
    ("aws-waf", ("x-amzn-waf-action", "x-amzn-requestid", "x-amzn-trace-id"), ("aws waf", "request blocked")),
    ("azure", ("x-azure-ref", "x-fd-int"), ("azure front door", "azure waf")),
    ("fastly", ("x-fastly-request-id", "x-served-by", "x-timer"), ("fastly",)),
    ("modsecurity", ("x-mod-security", "server"), ("mod_security", "modsecurity", "not acceptable")),
    ("barracuda", ("x-barracuda",), ("barracuda",)),
    ("wallarm", ("x-wallarm",), ("wallarm",)),
)

_SOFT_404 = (
    "not found", "404 not found", "page not found", "does not exist", "no longer available",
    "cannot be found", "tidak ditemukan",
)

_STATUS_BLOCK = frozenset({403, 406, 451, 999})
_STATUS_AUTH = frozenset({401, 407})


@dataclass(frozen=True)
class Signal:
    kind: str          # blocked | waf | rate_limited | auth_required | soft_not_found | success | redirect | error
    vendor: str
    confidence: float
    reason: str
    status: int

    @property
    def is_blocked(self) -> bool:
        return self.kind in {"blocked", "waf", "rate_limited", "auth_required"}


def _headers_lower(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}


def _vendor(headers: Mapping[str, str], body: str) -> tuple[str, str]:
    hl = _headers_lower(headers)
    blob = body.lower()
    for name, needles, body_needles in _VENDORS:
        for header in needles:
            if header in hl:
                if header == "server" and name == "modsecurity" and "mod_security" not in hl[header]:
                    continue
                return name, f"header {header}"
        for needle in body_needles:
            if needle in blob:
                return name, f"body signature {needle!r}"
    return "", ""


def classify(
    status: int,
    headers: Mapping[str, str] | None = None,
    body: str = "",
    *,
    analysis: str = "",
    baseline_status: int = 0,
) -> Signal:
    """Classify one response. Order matters: auth/rate edges first, then WAF."""
    status = int(status or 0)
    body_l = (body or "")[:200_000].lower()
    analysis_l = (analysis or "")[:20_000].lower()
    vendor, why = _vendor(headers or {}, body_l + "\n" + analysis_l)

    if status == 429:
        return Signal("rate_limited", vendor, 0.95, "HTTP 429", status)
    if status in _STATUS_AUTH:
        return Signal("auth_required", vendor, 0.95, f"HTTP {status}", status)
    if status in _STATUS_BLOCK:
        return Signal("blocked", vendor, 0.95, f"HTTP {status}", status)
    if vendor:
        return Signal("waf", vendor, 0.9, why, status)

    if status in (503, 520, 521, 522, 523, 524, 525, 526):
        return Signal("blocked", "cloudflare" if status >= 520 else vendor, 0.8, f"edge HTTP {status}", status)

    if any(needle in body_l or needle in analysis_l for needle in _BODY_BLOCK):
        return Signal("waf", vendor, 0.85, "body block signature", status)

    if status == 200 and baseline_status in (404, 410):
        return Signal("soft_not_found", vendor, 0.7, "200 over 404 baseline", status)
    if status == 200 and any(needle in body_l[:4000] for needle in _SOFT_404):
        return Signal("soft_not_found", vendor, 0.55, "soft-404 body", status)

    if 300 <= status < 400:
        return Signal("redirect", vendor, 0.75, f"HTTP {status}", status)
    if 200 <= status < 300:
        return Signal("success", vendor, 0.9, f"HTTP {status}", status)
    if status >= 500:
        return Signal("error", vendor, 0.6, f"HTTP {status}", status)
    return Signal("error", vendor, 0.5, f"HTTP {status or 'no response'}", status)


def looks_blocked(status: int, headers: Mapping[str, str] | None = None, body: str = "", analysis: str = "") -> bool:
    return classify(status, headers, body, analysis=analysis).is_blocked
