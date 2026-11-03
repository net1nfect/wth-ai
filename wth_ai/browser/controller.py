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
    page_text: str = ""       # RAW HTML, truncated to 50k -- see navigate()
    # Rendered, hydrated text of the page. Measured: a docs page had 1.27 MB of
    # HTML (so `page_text` kept only the first 5%, i.e. head/nav/scripts) while
    # `document.body.innerText` held 23k characters of the actual article. Prose
    # consumers must read THIS field, not parsed slices of the truncated HTML.
    visible_text: str = ""
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

    async def navigate(
        self,
        url: str,
        *,
        wait_seconds: float = 5.0,
        bypass_cloudflare: bool = True,
        wait_for: str = "",
    ) -> BrowserSession:
        """Open `url` and capture the page.

        `wait_for` is a CSS selector to AWAIT before capturing. Measured: on a
        client-rendered docs page (nextjs.org) a fixed `wait_seconds` captured
        the site chrome ("Frameworks / Svelte / Nuxt …") with the article body
        absent -- 545 characters of menu, because the DOM had not hydrated. A
        selector wait is what turns "the browser opened it" into "we read it".
        """
        await self.start()
        session = BrowserSession(url=url)
        try:
            self._tab = await self._browser.get(url)
            await asyncio.sleep(wait_seconds)
            if wait_for:
                await self._await_selector(session, wait_for)

            html = await self._tab.get_content() or ""
            session.page_text = html[:50000]
            session.visible_text = await self._capture_visible_text()
            session.cloudflare_detected = self._detect_cloudflare(session.page_text)

            if session.cloudflare_detected and bypass_cloudflare:
                await asyncio.sleep(8)
                html = await self._tab.get_content() or ""
                session.page_text = html[:50000]
                session.visible_text = await self._capture_visible_text()
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

    async def _capture_visible_text(self) -> str:
        """Rendered text of the page (`document.body.innerText`).

        This is the content channel: parsing `get_content()` gives HTML that the
        controller truncates to 50k, and on a 1.27 MB docs page the article lives
        far beyond that cut -- measured 545 characters of navigation chrome from
        the HTML versus 23,357 characters of real article from innerText.
        """
        try:
            text = await self._tab.evaluate("document.body.innerText")
            return (text or "")[:200_000]
        except Exception as exc:  # noqa: BLE001 - fall back to the HTML channel
            return ""

    async def _await_selector(self, session: BrowserSession, selector: str) -> None:
        """Wait for `selector`, recording (not raising) when it never appears.

        A timeout here is informational: the page may legitimately have no
        `<article>`/`<main>`, and the caller still gets whatever rendered. What
        must NOT happen is a silent fixed sleep being reported as a page read.
        """
        try:
            await self._tab.select(selector, timeout=15)
        except Exception as exc:  # noqa: BLE001
            session.errors.append(f"wait_for {selector!r} gagal: {type(exc).__name__}")

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


# Modes allowed to drive the browser. UNRESTRICTED is the most permissive
# dimension-2 (capability) label, so this allow-list has to be a superset of
# the lower modes -- omitting it made the MOST permissive mode the only one
# that could not browse at all (orchestrator.py::_browser_probe gates on this).
_BROWSER_ALLOWED_MODES = frozenset({"standard", "elevated", "full_lab", "unrestricted"})


def browser_allowed(permission_mode: PermissionMode) -> bool:
    return permission_mode.value in _BROWSER_ALLOWED_MODES
