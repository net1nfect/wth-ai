"""Browser automation via nodriver — Cloudflare bypass & deep testing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from wth_ai.models import PermissionMode


@dataclass
class BrowserSession:
    url: str
    title: str = ""
    cookies: list[dict[str, Any]] = field(default_factory=list)
    page_text: str = ""
    cloudflare_detected: bool = False
    cloudflare_bypassed: bool = False
    forms: list[dict[str, Any]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class BrowserController:
    """Headless browser — Cloudflare bypass, login/register, DOM inspection."""

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._browser = None
        self._tab = None

    async def start(self) -> None:
        import nodriver as uc
        if self._browser is None:
            self._browser = await uc.start(headless=self.headless)

    async def stop(self) -> None:
        if self._browser:
            try:
                self._browser.stop()
            except Exception:
                pass
        self._browser = None
        self._tab = None

    async def navigate(self, url: str, *, wait_seconds: float = 5.0, bypass_cloudflare: bool = True) -> BrowserSession:
        await self.start()
        session = BrowserSession(url=url)
        try:
            self._tab = await self._browser.get(url)
            await asyncio.sleep(wait_seconds)

            html = await self._tab.get_content() or ""
            session.page_text = html[:50000]
            session.cloudflare_detected = self._detect_cloudflare(session.page_text)

            if session.cloudflare_detected and bypass_cloudflare:
                await asyncio.sleep(8)
                html = await self._tab.get_content() or ""
                session.page_text = html[:50000]
                session.cloudflare_bypassed = not self._detect_cloudflare(session.page_text)

            try:
                session.title = await self._tab.evaluate("document.title") or ""
            except Exception:
                pass

            session.forms = await self._extract_forms()
            session.links = await self._extract_links()
        except Exception as exc:
            session.errors.append(str(exc))
        return session

    def _detect_cloudflare(self, html: str) -> bool:
        markers = ["cf-browser-verification", "challenge-platform", "Just a moment",
                   "Checking your browser", "cf-turnstile", "turnstile"]
        lower = html.lower()
        return any(m.lower() in lower for m in markers)

    async def _extract_forms(self) -> list[dict[str, Any]]:
        if not self._tab:
            return []
        try:
            return await self._tab.evaluate("""
                () => Array.from(document.querySelectorAll('form')).slice(0, 10).map(f => ({
                    action: f.action, method: f.method,
                    inputs: Array.from(f.querySelectorAll('input,select,textarea')).slice(0, 20)
                        .map(i => ({name: i.name, type: i.type || i.tagName.toLowerCase()}))
                }))
            """) or []
        except Exception:
            return []

    async def _extract_links(self) -> list[str]:
        if not self._tab:
            return []
        try:
            return await self._tab.evaluate("""
                () => Array.from(document.querySelectorAll('a[href]')).slice(0, 50)
                    .map(a => a.href).filter(h => h.startsWith('http'))
            """) or []
        except Exception:
            return []

    async def login_flow(self, url: str, username: str, password: str) -> dict[str, Any]:
        session = await self.navigate(url, bypass_cloudflare=True)
        if not self._tab:
            return {"success": False, "error": "Browser tab failed"}

        try:
            for sel in ["input[type=email]", "input[name=email]", "input[name=username]", "#email"]:
                el = await self._tab.select(sel, timeout=3)
                if el:
                    await el.send_keys(username)
                    break
            for sel in ["input[type=password]", "input[name=password]", "#password"]:
                el = await self._tab.select(sel, timeout=3)
                if el:
                    await el.send_keys(password)
                    break
            for sel in ["button[type=submit]", "input[type=submit]", "button.login"]:
                btn = await self._tab.select(sel, timeout=3)
                if btn:
                    await btn.click()
                    break
            await asyncio.sleep(5)
            html = await self._tab.get_content() or ""
            return {
                "success": True,
                "logged_in_hint": any(k in html.lower() for k in ("logout", "dashboard", "welcome")),
                "cloudflare_bypassed": session.cloudflare_bypassed,
                "page_snippet": html[:3000],
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}


def browser_allowed(permission_mode: PermissionMode) -> bool:
    return permission_mode.value in {"standard", "elevated", "full_lab"}
