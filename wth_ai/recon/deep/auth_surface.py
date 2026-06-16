"""Detect authentication surfaces from crawled URLs."""

from __future__ import annotations

import re
from typing import Any

AUTH_PATTERNS = {
    "login": re.compile(r"(?i)(/login|/signin|/auth/login|/session/new)"),
    "register": re.compile(r"(?i)(/register|/signup|/sign-up|/create-account)"),
    "reset": re.compile(r"(?i)(/reset|/forgot|/password/reset|/recover)"),
    "oauth": re.compile(r"(?i)(/oauth|/callback|/auth/callback|/sso)"),
    "logout": re.compile(r"(?i)(/logout|/signout)"),
}


class AuthSurfaceDetector:
    def detect(self, urls: list[str]) -> list[dict[str, str]]:
        flows: list[dict[str, str]] = []
        seen: set[str] = set()
        for url in urls:
            for flow_type, pat in AUTH_PATTERNS.items():
                if pat.search(url) and url not in seen:
                    flows.append({"url": url, "flow_type": flow_type})
                    seen.add(url)
        return flows

    def summary(self, urls: list[str]) -> dict[str, Any]:
        flows = self.detect(urls)
        by_type: dict[str, list[str]] = {}
        for f in flows:
            by_type.setdefault(f["flow_type"], []).append(f["url"])
        return {"flows": flows, "by_type": by_type, "count": len(flows)}
