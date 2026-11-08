"""Multi-account HTTP session engine — cookie jars, CSRF, rate limit."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from wth_ai.models import Scope


@dataclass
class SessionRole:
    name: str
    username: str
    password: str
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    csrf_token: str = ""
    jwt: str = ""

    def auth_header(self) -> dict[str, str]:
        if self.jwt:
            return {"Authorization": f"Bearer {self.jwt}"}
        return {}


class SessionEngine:
    """Manage per-role HTTP sessions for BOLA/IDOR and auth testing."""

    def __init__(self, scope: Scope, *, rate_limit_rps: float = 2.0) -> None:
        self.scope = scope
        self.rate_limit_rps = rate_limit_rps or scope.rate_limit_rps
        self._last_request = 0.0
        self.roles: dict[str, SessionRole] = {}
        self._parse_accounts()

    def _parse_accounts(self) -> None:
        for i, raw in enumerate(self.scope.test_accounts):
            if ":" not in raw:
                continue
            user, passwd = raw.split(":", 1)
            name = f"user_{chr(97 + i)}" if i < 26 else f"user_{i}"
            self.roles[name] = SessionRole(name=name, username=user.strip(), password=passwd.strip())

    def _throttle(self) -> None:
        if self.rate_limit_rps <= 0:
            return
        min_gap = 1.0 / self.rate_limit_rps
        elapsed = time.monotonic() - self._last_request
        if elapsed < min_gap:
            time.sleep(min_gap - elapsed)
        self._last_request = time.monotonic()

    def _session_for(self, role: SessionRole) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "WTH-AI-SessionEngine/1.0",
            **role.headers,
            **role.auth_header(),
        })
        if role.cookies:
            s.cookies.update(role.cookies)
        return s

    def request(
        self,
        role_name: str,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 15,
    ) -> dict[str, Any]:
        role = self.roles.get(role_name)
        if not role:
            return {"error": f"role {role_name} not configured", "status": 0, "body": ""}
        self._throttle()
        sess = self._session_for(role)
        hdrs = dict(headers or {})
        if role.csrf_token and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            hdrs.setdefault("X-CSRF-Token", role.csrf_token)
        try:
            r = sess.request(
                method.upper(), url, params=params, data=data, json=json_body,
                headers=hdrs, timeout=timeout, allow_redirects=True,
            )
            return {
                "status": r.status_code,
                "headers": dict(r.headers),
                "body": r.text[:50_000],
                "url": r.url,
            }
        except requests.RequestException as exc:
            return {"error": str(exc), "status": 0, "body": ""}

    def login_form(
        self,
        role_name: str,
        login_url: str,
        *,
        user_field: str = "email",
        pass_field: str = "password",
        extra: dict[str, str] | None = None,
    ) -> bool:
        role = self.roles.get(role_name)
        if not role:
            return False
        self._throttle()
        sess = self._session_for(role)
        try:
            page = sess.get(login_url, timeout=15)
            csrf = self._extract_csrf(page.text)
            if csrf:
                role.csrf_token = csrf
            payload = {user_field: role.username, pass_field: role.password, **(extra or {})}
            if csrf:
                payload.setdefault("_token", csrf)
                payload.setdefault("csrf_token", csrf)
            r = sess.post(login_url, data=payload, timeout=15, allow_redirects=True)
            role.cookies = dict(sess.cookies)
            role.headers.update(dict(sess.headers))
            jwt = self._extract_jwt(r.text) or r.headers.get("Authorization", "")
            if jwt:
                role.jwt = jwt.replace("Bearer ", "")
            return r.status_code in {200, 302, 303} and "login" not in r.url.lower()
        except requests.RequestException:
            return False

    def _extract_csrf(self, html: str) -> str:
        for pat in (
            r'name=["\']_token["\']\s+value=["\']([^"\']+)',
            r'name=["\']csrf[_-]?token["\']\s+value=["\']([^"\']+)',
            r'"csrfToken"\s*:\s*"([^"]+)"',
        ):
            m = re.search(pat, html, re.I)
            if m:
                return m.group(1)
        return ""

    def _extract_jwt(self, text: str) -> str:
        m = re.search(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", text)
        return m.group(0) if m else ""

    def diff_responses(self, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        return {
            "status_diff": a.get("status") != b.get("status"),
            "length_diff": abs(len(a.get("body", "")) - len(b.get("body", ""))),
            "bodies_differ": a.get("body", "") != b.get("body", ""),
            "a_status": a.get("status"),
            "b_status": b.get("status"),
        }

    def configured(self) -> bool:
        return len(self.roles) >= 1

    def role_names(self) -> list[str]:
        return list(self.roles.keys())
