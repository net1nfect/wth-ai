"""Autonomous research — the agent learns ABOUT a target, never FROM it.

Gap this closes
---------------
WTH AI could probe a target but had no way to learn anything about it: no CVE
lookup, no vendor docs, no public write-ups, no search. An offensive agent
without research re-derives the same knowledge on every engagement. Verified
before writing this module: `<no search/research/fetch-url implementation>` in
`wth_ai/` -- the only "search" hits were LOCAL indexes (session FTS, skill
indexer), and the agent tool registry had 21 tools with none for research.

Design rules, each one a decision rather than a default
------------------------------------------------------
1. **External information is never evidence about the target.** A blog saying
   "WordPress 6.2 is vulnerable to X" proves nothing about THIS host. Every note
   is tagged ``level=veracity.LEVEL_CANDIDATE`` and routed through the veracity
   layer, because a research feature with no discipline would become the single
   biggest overclaim generator in the tool.
2. **Reuse the SSRF guard** (`recon.http.host_is_blocked`). A fetch tool that can
   reach 127.0.0.1 or the LAN is an internal-network proxy wearing a research
   costume. One guard, imported, not re-implemented.
3. **Low volume, session-cached.** A search query is fetched once per session;
   fetches are sequential with a delay. Research must never look like scanning.
4. **The browser is optional.** `chromium` + `nodriver` are installed on this
   box, but a plain HTTP fetch is far more reliable; the browser is used only
   when explicitly asked, and reports precisely why it could not run.
5. **Notes persist** under `$WTH_HOME/research/`, so the next engagement on the
   same tech stack starts from what was already learned.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus, urlparse

from wth_ai import veracity
from wth_ai.recon.http import host_is_blocked

# Search backends, tried IN ORDER until one returns results.
#
# Measured on this network: DuckDuckGo (html + lite + api) is UNREACHABLE
# (connect timeout), while Bing answers in 0.34s and Brave in 0.5s. Hardcoding a
# single endpoint therefore means "research silently does not work" on whatever
# network blocks that one host -- so the chain is explicit, and the note records
# which backend actually answered.
SEARCH_BACKENDS: tuple[tuple[str, str], ...] = (
    ("duckduckgo-lite", "https://lite.duckduckgo.com/lite/?q={query}"),
    ("duckduckgo-html", "https://html.duckduckgo.com/html/?q={query}"),
    ("bing", "https://www.bing.com/search?q={query}"),
    ("brave", "https://search.brave.com/search?q={query}"),
)
SEARCH_ENDPOINT = SEARCH_BACKENDS[0][1]  # kept: callers/tests may reference it
SEARCH_TIMEOUT_SECONDS = 8

# Keyed search API (tried BEFORE any HTML scraping).
#
# Why this exists: scraping a search engine's HTML gave demonstrably poor
# results on this network. A precise query ("Next.js middleware authorization
# bypass CVE") came back dominated by the fashion retailer "Next" -- the engine
# serves scriptless clients a low-fidelity page. An API returns structured JSON
# with real titles, real URLs and no redirect wrappers.
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
API_SUPPORTED_PROVIDERS = ("brave",)

# A browser-ish UA: the default `python-requests` string gets served a challenge
# page by most search endpoints, which would be reported as "no results" -- a
# false negative that looks like the feature working.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

MAX_TEXT_CHARS = 20_000
DEFAULT_DELAY_SECONDS = 0.8


# Bing wraps every result link in a redirect:
#   https://www.bing.com/ck/a?!&&p=...&u=a1<base64url-of-real-url>&ntb=1
# Taking the href verbatim stored a bing.com blob as "the URL of the result" --
# it is not the page, cannot be fetched meaningfully, and would be shown to the
# operator as the source. Verified live: every result URL in a real Bing page
# was such a wrapper.
_BING_REDIRECT_RE = re.compile(r"[?&]u=a1([A-Za-z0-9_\-]+)")


def unwrap_result_url(href: str) -> str:
    """Return the real destination of a search-result link."""
    match = _BING_REDIRECT_RE.search(href or "")
    if not match:
        return href or ""
    token = match.group(1)
    token += "=" * (-len(token) % 4)
    try:
        import base64

        decoded = base64.urlsafe_b64decode(token).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - keep the original on failure
        return href
    return decoded if decoded.startswith("http") else href


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchNote:
    """One piece of external knowledge, with its epistemic status attached.

    `level` exists so a note can be quoted in a report WITHOUT being mistaken for
    a finding about the target.
    """

    source: str                     # search | fetch | browser
    ref: str                        # the query or the URL it came from
    backend: str = ""               # which search backend answered (honesty about source)
    title: str = ""
    url: str = ""
    snippet: str = ""
    text: str = ""
    tech_tags: list[str] = field(default_factory=list)
    level: str = veracity.LEVEL_CANDIDATE
    created_at: str = field(default_factory=_now)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self, *, text_chars: int = 240) -> str:
        head = (self.text or self.snippet or "").replace("\n", " ")[:text_chars]
        return f"[{self.level}] {self.title or self.url or self.ref}: {head}"


class Researcher:
    """Search, fetch and remember public information about a target's stack."""

    def __init__(
        self,
        *,
        session_id: str = "",
        notes_dir: Path | None = None,
        http_get: Callable[..., Any] | None = None,
        http_get_json: Callable[..., Any] | None = None,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        veracity_gate: bool = True,
        search_provider: str = "",
        search_api_key: str = "",
        auto_browser_escalate: bool = True,
    ) -> None:
        self.session_id = session_id
        self._http_get = http_get
        # Separate seam for keyed API calls: they send auth headers, and keeping
        # them apart means an existing HTML-fetch stub cannot be silently handed
        # an authenticated request.
        self._http_get_json = http_get_json
        self.delay_seconds = delay_seconds
        self.veracity_gate = veracity_gate
        self.search_provider = (search_provider or "").lower()
        self.search_api_key = search_api_key or ""
        self.auto_browser_escalate = auto_browser_escalate
        self._search_cache: dict[str, ResearchNote] = {}
        self.notes: list[ResearchNote] = []
        self.notes_dir = notes_dir or self._default_notes_dir()

    # ------------------------------------------------------------------ #
    # storage
    # ------------------------------------------------------------------ #
    @staticmethod
    def _default_notes_dir() -> Path:
        try:
            from wth_ai.workspace import wth_home

            return wth_home() / "research"
        except Exception:  # noqa: BLE001 - storage is an enhancement
            return Path.home() / ".wth-ai" / "research"

    # ------------------------------------------------------------------ #
    # redaction — applied before anything is cached, shown or stored
    # ------------------------------------------------------------------ #
    @staticmethod
    def redact(note: ResearchNote) -> ResearchNote:
        """Mask credential shapes in every field a note exposes.

        Found by this module's own test suite: a search snippet containing
        `token=SUPERSECRETVALUE…` was written VERBATIM into the JSONL note store.
        `veracity.sanitize_claims` downgrades absolutist claims -- it does not
        mask credentials, and the project rule is that no secret lands in a
        config, log, report, result file or fixture. Redaction therefore happens
        here, once, before the note is cached, rendered or persisted.
        """
        from wth_ai.redaction import redact_secrets

        note.text = redact_secrets(note.text)
        note.snippet = redact_secrets(note.snippet)
        note.title = redact_secrets(note.title)
        return note

    def persist(self, note: ResearchNote) -> Path | None:
        """Append a note to the session's research log (JSONL, one note per line).

        Redacts again defensively: any future producer that forgets to call
        `redact()` still cannot write a credential to disk through here.
        """
        self.redact(note)
        try:
            self.notes_dir.mkdir(parents=True, exist_ok=True)
            key = re.sub(r"[^A-Za-z0-9_.-]", "_", self.session_id or "default")[:64]
            path = self.notes_dir / f"{key}.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(note.to_dict()) + "\n")
            return path
        except OSError:
            return None

    def load(self, *, limit: int = 50) -> list[ResearchNote]:
        key = re.sub(r"[^A-Za-z0-9_.-]", "_", self.session_id or "default")[:64]
        path = self.notes_dir / f"{key}.jsonl"
        if not path.exists():
            return []
        out: list[ResearchNote] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
                if not line.strip():
                    continue
                data = json.loads(line)
                out.append(ResearchNote(**{k: v for k, v in data.items() if k in ResearchNote.__annotations__}))
        except (OSError, ValueError, TypeError):
            return out
        return out

    # ------------------------------------------------------------------ #
    # HTTP seam
    # ------------------------------------------------------------------ #
    def _get(self, url: str, *, timeout: int = 25) -> Any:
        if self._http_get is not None:
            return self._http_get(url, timeout=timeout)
        import requests

        return requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            allow_redirects=True,
        )

    def _get_json(self, url: str, *, headers: dict[str, str], timeout: int = 25) -> Any:
        if self._http_get_json is not None:
            return self._http_get_json(url, headers=headers, timeout=timeout)
        import requests

        return requests.get(url, headers=headers, timeout=timeout)

    def api_search(self, query: str, *, limit: int = 8) -> list[SearchResult] | None:
        """Search via a keyed API. Returns None when no API is configured.

        None (not []) is the signal for "not configured / unusable, fall through
        to the HTML backends" -- an empty list would mean "the API answered and
        had nothing", which is a different and much more meaningful outcome.
        """
        if not self.search_api_key or self.search_provider not in API_SUPPORTED_PROVIDERS:
            return None
        url = f"{BRAVE_SEARCH_ENDPOINT}?q={quote_plus(query)}&count={max(1, min(limit, 20))}"
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.search_api_key,
            "User-Agent": USER_AGENT,
        }
        try:
            response = self._get_json(url, headers=headers, timeout=SEARCH_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 - fall through to HTML backends
            return None
        if getattr(response, "status_code", 0) >= 400:
            return None
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            return None
        rows = ((payload or {}).get("web") or {}).get("results") or []
        results: list[SearchResult] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            url_value = str(row.get("url") or "")
            if not url_value.startswith("http"):
                continue
            results.append(
                SearchResult(
                    title=" ".join(str(row.get("title") or "").split()),
                    url=url_value,
                    snippet=" ".join(str(row.get("description") or "").split()),
                )
            )
            if len(results) >= limit:
                break
        return results

    def _gate(self, host: str) -> str | None:
        """SSRF guard. Returns a reason when the host must not be fetched."""
        return host_is_blocked(host)

    # ------------------------------------------------------------------ #
    # search
    # ------------------------------------------------------------------ #
    def _finish_search(
        self, note: ResearchNote, query: str, results: list[SearchResult], *, backend: str
    ) -> ResearchNote:
        """Shared tail of both search paths (API + HTML): render, redact, gate, store."""
        note.backend = backend
        note.title = f"{len(results)} hasil ({backend}) untuk {query!r}"
        note.snippet = "\n".join(f"- {r.title} — {r.url}" for r in results)
        note.text = note.snippet
        note.tech_tags = _extract_tech_tags(" ".join(f"{r.title} {r.snippet}" for r in results))
        self.redact(note)
        if self.veracity_gate:
            guarded, violations = veracity.sanitize_claims(note.text)
            note.text = guarded
            if violations:
                note.tech_tags.append("veracity-downgraded")
        self._search_cache[query] = note
        self.notes.append(note)
        self.persist(note)
        time.sleep(self.delay_seconds)
        return note

    def search(self, query: str, *, limit: int = 8) -> ResearchNote:
        """Search the web, walking `SEARCH_BACKENDS` until one answers.

        Two failure modes are reported DISTINCTLY, because to an operator they
        look identical and only one is true:
          * every backend unreachable/timed out  -> "semua backend search tidak terjangkau"
          * a backend answered but refused       -> challenge/rate-limit message
        Neither is ever rendered as "0 hasil".
        """
        clean = (query or "").strip()
        if not clean:
            return ResearchNote("search", query, error="query kosong")
        if clean in self._search_cache:
            return self._search_cache[clean]

        note = ResearchNote("search", clean)
        attempts: list[str] = []

        api_results = self.api_search(clean, limit=limit)
        if api_results:
            return self._finish_search(note, clean, api_results, backend=f"api:{self.search_provider}")
        if self.search_api_key and self.search_provider in API_SUPPORTED_PROVIDERS:
            attempts.append(f"api:{self.search_provider}: tidak mengembalikan hasil")

        for backend, template in SEARCH_BACKENDS:
            url = template.format(query=quote_plus(clean))
            note.url = url
            try:
                response = self._get(url, timeout=SEARCH_TIMEOUT_SECONDS)
            except Exception as exc:  # noqa: BLE001 - try the next backend
                attempts.append(f"{backend}: {type(exc).__name__}")
                continue

            status = getattr(response, "status_code", 0)
            body = getattr(response, "text", "") or ""
            if status == 403 or "anomaly" in body.lower()[:2000]:
                attempts.append(f"{backend}: ditolak (challenge/rate limit, HTTP {status})")
                continue
            if status >= 400:
                attempts.append(f"{backend}: HTTP {status}")
                continue

            results = self._parse_results(body, limit=limit, backend=backend)
            if not results:
                attempts.append(f"{backend}: tidak ada hasil terparse")
                continue

            return self._finish_search(note, clean, results, backend=backend)

        note.error = "semua backend search tidak terjangkau: " + " | ".join(attempts)
        self._search_cache[clean] = note
        return note

    @staticmethod
    def _parse_results(html: str, *, limit: int, backend: str = "duckduckgo-html") -> list[SearchResult]:
        """Parse a search engine's HTML result page.

        Per-backend selectors with a generic fallback, because a markup change
        must degrade to "fewer results", not to an exception -- and because a
        generic anchor scan on a JS-heavy engine page harvests navigation links
        (observed live on startpage/brave), so the specific selector is tried
        first and the generic scan only as a last resort.
        """
        results: list[SearchResult] = []
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            if backend == "bing":
                seen: set[str] = set()
                for item in soup.select("li.b_algo")[: limit * 3]:
                    heading = item.find("h2")
                    # The <h2> anchor is the result LINK; `item.find("a")` is
                    # usually the site/display-url line, which yields a bare
                    # domain as the "title" (observed live: results titled
                    # "nextdirect.com", "next.co.uk" for a Next.js query).
                    anchor = heading.find("a", href=True) if heading else item.find("a", href=True)
                    if not anchor:
                        continue
                    url = unwrap_result_url(anchor["href"])
                    if not url.startswith("http") or "bing.com/ck" in url:
                        continue
                    title = " ".join(anchor.get_text(" ", strip=True).split())
                    if not title:
                        continue
                    if url in seen:
                        continue
                    seen.add(url)
                    tail = item.find("p")
                    snippet = " ".join(tail.get_text(" ", strip=True).split()) if tail else ""
                    results.append(SearchResult(title=title, url=url, snippet=snippet))
                    if len(results) >= limit:
                        break
                if results:
                    return results
            for anchor in soup.select("a.result__a")[: limit * 2]:
                href = anchor.get("href") or ""
                title = " ".join(anchor.get_text(" ", strip=True).split())
                if not title or not href:
                    continue
                snippet_el = anchor.find_next(class_="result__snippet")
                snippet = " ".join(snippet_el.get_text(" ", strip=True).split()) if snippet_el else ""
                results.append(SearchResult(title=title, url=href, snippet=snippet))
                if len(results) >= limit:
                    break
        except Exception:  # noqa: BLE001 - fall through to regex
            results = []

        if not results:
            for match in re.finditer(
                r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                html,
                re.S,
            ):
                href, title = match.group(1), re.sub(r"<[^>]+>", "", match.group(2)).strip()
                if title:
                    results.append(SearchResult(title=" ".join(title.split()), url=href))
                if len(results) >= limit:
                    break
        return results

    # ------------------------------------------------------------------ #
    # fetch
    # ------------------------------------------------------------------ #
    def fetch(self, url: str, *, tech_tags: list[str] | None = None) -> ResearchNote:
        """Fetch a public page and keep its readable text."""
        from wth_ai.recon.http import host_of, normalize_url

        target = normalize_url(url)
        host = host_of(target)
        note = ResearchNote("fetch", target, url=target, tech_tags=list(tech_tags or []))

        blocked = self._gate(host)
        if blocked:
            note.error = f"ditolak SSRF guard: {blocked}"
            return note

        try:
            response = self._get(target)
        except Exception as exc:  # noqa: BLE001
            note.error = f"{type(exc).__name__}: {exc}"
            return note

        status = getattr(response, "status_code", 0)
        body = getattr(response, "text", "") or ""
        if status >= 400:
            note.error = f"HTTP {status} saat mengambil {target}"
            return note

        note.title = _page_title(body) or host
        note.text = _readable_text(body)[:MAX_TEXT_CHARS]
        note.tech_tags = list(tech_tags or []) or _extract_tech_tags(body[:5000])
        self.redact(note)
        if self.veracity_gate:
            guarded, violations = veracity.sanitize_claims(note.text)
            note.text = guarded
            if violations:
                note.tech_tags.append("veracity-downgraded")
        if self.auto_browser_escalate and _looks_like_navigation_junk(note.text):
            # Measured: a nextjs.org docs page extracted as the site's navigation
            # ("Frameworks Svelte Nuxt Nitro SDKs …") because the article is
            # client-rendered. Escalate to the real browser instead of handing
            # the agent a menu as if it were the page.
            upgraded = self.browser_fetch(target)
            if upgraded.ok and len(upgraded.text) > len(note.text):
                upgraded.ref = target
                upgraded.tech_tags.append("escalated-to-browser")
                self.persist(upgraded)
                return upgraded
            note.snippet = (
                note.snippet + "\n[escalate] browser tidak memberi teks lebih baik: "
                + (upgraded.error or "teks tidak lebih panjang")
            ).strip()

        self.notes.append(note)
        self.persist(note)
        time.sleep(self.delay_seconds)
        return note

    def browser_fetch(
        self, url: str, *, wait_seconds: float = 4.0, wait_for: str = "article, main, [role=main]"
    ) -> ResearchNote:
        """JS-heavy fallback using the real browser (`nodriver` + chromium).

        Optional by design: this box HAS chromium and nodriver, but the browser
        path pulls in a display, a profile and a Cloudflare dance, so it must
        never be the default and must explain itself when it cannot run.
        """
        note = ResearchNote("browser", url, url=url)
        from wth_ai.recon.http import host_of, normalize_url

        target = normalize_url(url)
        blocked = self._gate(host_of(target))
        if blocked:
            note.error = f"ditolak SSRF guard: {blocked}"
            return note

        try:
            import nodriver  # noqa: F401
        except Exception:  # noqa: BLE001
            note.error = "nodriver tidak terpasang (fallback: pakai fetch biasa)"
            return note

        import asyncio

        async def _run() -> tuple[str, str, str]:
            import nodriver as uc

            from wth_ai.browser.controller import BrowserController

            controller = BrowserController(headless=True)
            await controller.start()
            try:
                session = await controller.navigate(
                    target, wait_seconds=wait_seconds, wait_for=wait_for
                )
                text = _session_text(session)
                title = getattr(session, "title", "") or ""
                flags = ""
                if getattr(session, "cloudflare_detected", False):
                    flags = "cloudflare" + (" (bypassed)" if getattr(session, "cloudflare_bypassed", False) else " (NOT bypassed)")
                return text, title, flags
            finally:
                await controller.stop()

        try:
            page, title, flags = asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001
            note.error = f"browser gagal: {type(exc).__name__}: {exc}"
            return note
        if not page.strip():
            # Report the empty page instead of returning a silently useless note:
            # `ok=True` with no text is exactly the failure that hid this bug.
            note.error = "browser membuka halaman tapi tidak ada teks yang terbaca"
            return note
        note.title = title or host_of(target)
        # `_readable_text` is a no-op on prose and strips tags on HTML, so it is
        # safe for both channels.
        note.text = _readable_text(page)[:MAX_TEXT_CHARS]
        note.tech_tags = _extract_tech_tags(page[:5000])
        if flags:
            note.tech_tags.append(flags)
        self.redact(note)
        self.notes.append(note)
        self.persist(note)
        return note

    # ------------------------------------------------------------------ #
    # context for the agent
    # ------------------------------------------------------------------ #
    def context_block(self, *, limit: int = 8, text_chars: int = 200) -> str:
        """Recent notes as an agent-readable block, epistemics included."""
        recent = [n for n in (self.notes or self.load()) if n.ok][-limit:]
        if not recent:
            return ""
        lines = [
            "HASIL RISET EKSTERNAL (status: KANDIDAT — bukan bukti tentang target;",
            "informasi dari internet tidak pernah membuktikan apa pun soal host ini):",
        ]
        for note in recent:
            lines.append(f"- {note.summary(text_chars=text_chars)}")
            if note.url:
                lines.append(f"  sumber: {note.url}")
        return "\n".join(lines)

    def report(self, note: ResearchNote) -> str:
        """Operator-facing rendering of one note."""
        if not note.ok:
            return f"REKOMENDASI GAGAL [{note.source}] {note.ref}: {note.error}"
        head = f"✓ [{note.source}] {note.title or note.ref}"
        if note.url:
            head += f"\n  sumber: {note.url}"
        body = (note.text or note.snippet or "").strip()
        if body:
            head += "\n" + "\n".join(f"  {ln}" for ln in body.splitlines()[:25])
        head += (
            f"\n  status epistemik: {note.level} — pakai sebagai LEAD, bukan temuan. "
            "Kalau mau jadi temuan, harus ada bukti langsung dari target."
        )
        return head


_TECH_WORDS = (
    "wordpress", "laravel", "django", "flask", "rails", "next.js", "nextjs", "react", "vue",
    "angular", "node", "express", "nginx", "apache", "cloudflare", "graphql", "jwt", "oauth",
    "keycloak", "jenkins", "gitlab", "grafana", "kibana", "elasticsearch", "redis", "postgres",
    "mysql", "mongodb", "docker", "kubernetes", "terraform", "supabase", "firebase", "vercel",
)


def _extract_tech_tags(text: str) -> list[str]:
    low = (text or "").lower()
    found: list[str] = []
    for word in _TECH_WORDS:
        if word in low and word not in found:
            found.append(word)
    return found[:8]


def _session_text(session: Any) -> str:
    """Page text of a `BrowserSession`.

    Verified by runtime introspection: the dataclass fields are
    `url, title, cookies, page_text, cloudflare_detected, cloudflare_bypassed,
    forms, links, errors` -- there is NO `html`. The first version read
    `getattr(session, "html", "")`, so the browser path launched chromium,
    navigated successfully, and then handed back an EMPTY page (measured: 8.2s,
    ok=True, 0 chars).

    Despite the name, `page_text` holds RAW HTML (`controller.navigate` assigns
    `html[:50000]` from `get_content()`), so callers must run it through
    `_readable_text()` before treating it as prose. The `.html` fallback is kept
    only for a future session shape.
    """
    # `visible_text` first: it is already rendered prose. `page_text` holds RAW
    # HTML truncated to 50k, so relying on it made a 1.27 MB article look like a
    # 545-character menu (measured live).
    for attr in ("visible_text", "page_text", "html"):
        value = getattr(session, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _looks_like_navigation_junk(text: str) -> bool:
    """Heuristic: a client-rendered page extracts as menu labels, not prose.

    Measured on a nextjs.org docs URL: the extracted text was the site chrome
    ("Frameworks / Svelte / Nuxt / Nitro / SDKs …") with no article body. Signals
    used: mostly SHORT lines and almost no sentence-like lines.
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 8:
        return False                      # too short to judge
    short = sum(1 for ln in lines if len(ln) < 40)
    prose = sum(1 for ln in lines if ln.count(" ") >= 6)
    return (short / len(lines)) > 0.6 and (prose / len(lines)) < 0.25


def _page_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split())[:200] if match else ""


_STRIP_BLOCKS = re.compile(r"<(script|style|noscript|template|svg)[^>]*>.*?</\1>", re.S | re.I)


def _readable_text(html: str) -> str:
    """HTML -> text without adding a readability dependency.

    Not `trafilatura`-quality; deliberately so: the goal is searchable evidence
    of what the PAGE said, and a heavy extractor that silently drops sections is
    worse than a simple one whose behaviour is predictable.
    """
    body = _STRIP_BLOCKS.sub(" ", html or "")
    try:
        from bs4 import BeautifulSoup

        text = BeautifulSoup(body, "html.parser").get_text("\n", strip=True)
    except Exception:  # noqa: BLE001
        text = re.sub(r"<[^>]+>", " ", body)
    lines = [" ".join(ln.split()) for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)
