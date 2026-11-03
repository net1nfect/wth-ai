"""Tests for the autonomous research module.

Two properties matter more than the parsing details:
1. research NEVER becomes target evidence (every note is a candidate), and
2. a blocked/failed lookup is reported as such, never as "no results".
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai import veracity
from wth_ai.research import ResearchNote, Researcher, get_researcher, reset_researchers

DDG_HTML = """
<html><body>
<div class="result"><a class="result__a" href="https://example.com/wp-cve">WordPress 6.2 CVE writeup</a>
  <a class="result__snippet">Details of a Laravel and WordPress issue.</a></div>
<div class="result"><a class="result__a" href="https://vendor.test/docs">Vendor docs</a>
  <a class="result__snippet">How nginx and graphql interact.</a></div>
</body></html>
"""


class _Resp:
    def __init__(self, status: int = 200, text: str = "") -> None:
        self.status_code = status
        self.text = text


def _researcher(tmp_path: Path, calls: list, *, response=None) -> Researcher:
    def _get(url: str, *, timeout: int = 25):
        calls.append(url)
        if callable(response):
            return response(url)
        return response if response is not None else _Resp()

    return Researcher(
        session_id="test-session", notes_dir=tmp_path / "research", http_get=_get, delay_seconds=0
    )


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
def test_search_parses_results(tmp_path):
    calls: list = []
    res = _researcher(tmp_path, calls, response=_Resp(200, DDG_HTML))
    note = res.search("wordpress cve")
    assert note.ok
    assert "WordPress 6.2 CVE writeup" in note.text
    assert "https://example.com/wp-cve" in note.text


def test_search_results_are_candidates_never_evidence(tmp_path):
    res = _researcher(tmp_path, [], response=_Resp(200, DDG_HTML))
    note = res.search("anything")
    assert note.level == veracity.LEVEL_CANDIDATE
    assert note.level != veracity.LEVEL_CONFIRMED


def test_search_is_cached_per_session(tmp_path):
    calls: list = []
    res = _researcher(tmp_path, calls, response=_Resp(200, DDG_HTML))
    res.search("same query")
    res.search("same query")
    assert len(calls) == 1, "query yang sama harus di-cache, bukan di-fetch ulang"


def test_blocked_search_is_not_reported_as_no_results(tmp_path):
    """A challenge page and an empty result set look identical to an operator,
    and only one of them is true."""
    res = _researcher(tmp_path, [], response=_Resp(403, "<html>anomaly detected</html>"))
    note = res.search("blocked query")
    assert not note.ok
    assert "menolak" in note.error or "challenge" in note.error


def test_empty_query_is_rejected(tmp_path):
    res = _researcher(tmp_path, [], response=_Resp(200, DDG_HTML))
    note = res.search("   ")
    assert not note.ok
    assert "kosong" in note.error


def test_search_extracts_tech_tags(tmp_path):
    res = _researcher(tmp_path, [], response=_Resp(200, DDG_HTML))
    note = res.search("stack")
    assert "wordpress" in note.tech_tags
    assert "nginx" in note.tech_tags or "graphql" in note.tech_tags


# --------------------------------------------------------------------------- #
# fetch — SSRF guard + extraction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin", "http://10.0.0.5/", "http://169.254.169.254/latest/meta-data",
    "http://[::1]/", "http://192.168.1.1/",
])
def test_fetch_refuses_internal_addresses(tmp_path, url):
    """A research fetch that can reach the LAN is an internal proxy, not research."""
    calls: list = []
    res = _researcher(tmp_path, calls, response=_Resp(200, "should never be reached"))
    note = res.fetch(url)
    assert not note.ok
    assert "SSRF" in note.error
    assert calls == [], "tidak boleh ada request sama sekali ke alamat internal"


def test_fetch_extracts_readable_text_and_title(tmp_path):
    html = (
        "<html><head><title>Docs Page</title></head><body>"
        "<script>var secret=1;</script><style>.x{}</style>"
        "<h1>Nginx tuning</h1><p>Body text here.</p></body></html>"
    )
    res = _researcher(tmp_path, [], response=_Resp(200, html))
    note = res.fetch("https://vendor.test/docs")
    assert note.ok
    assert note.title == "Docs Page"
    assert "Body text here." in note.text
    assert "var secret" not in note.text, "script harus dibuang dari teks"


def test_fetch_reports_http_errors(tmp_path):
    res = _researcher(tmp_path, [], response=_Resp(404, "nope"))
    note = res.fetch("https://vendor.test/missing")
    assert not note.ok
    assert "404" in note.error


def test_fetch_applies_the_veracity_gate(tmp_path):
    """A page claiming a vulnerability is an unbacked absolutist claim until
    something about the TARGET proves it."""
    html = "<html><title>x</title><body>This product is confirmed vulnerable to SSRF.</body></html>"
    res = _researcher(tmp_path, [], response=_Resp(200, html))
    note = res.fetch("https://blog.test/post")
    assert "confirmed vulnerable" not in note.text
    assert "veracity-downgraded" in note.tech_tags


def test_fetch_connection_error_is_reported_not_raised(tmp_path):
    def _boom(url: str, *, timeout: int = 25):
        raise ConnectionError("no route to host")

    res = Researcher(session_id="s", notes_dir=tmp_path / "r", http_get=_boom, delay_seconds=0)
    note = res.fetch("https://unreachable.test/")
    assert not note.ok
    assert "ConnectionError" in note.error


def test_browser_fetch_without_nodriver_explains_itself(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "nodriver", None)
    res = _researcher(tmp_path, [], response=_Resp(200, "x"))
    note = res.browser_fetch("https://vendor.test/")
    assert not note.ok
    assert "nodriver" in note.error


# --------------------------------------------------------------------------- #
# persistence + agent context
# --------------------------------------------------------------------------- #
def test_notes_persist_and_reload(tmp_path):
    res = _researcher(tmp_path, [], response=_Resp(200, DDG_HTML))
    res.search("persist me")

    fresh = Researcher(session_id="test-session", notes_dir=tmp_path / "research")
    loaded = fresh.load()
    assert loaded, "catatan tidak tersimpan"
    assert "persist me" in loaded[0].ref


def test_context_block_states_the_epistemic_status(tmp_path):
    res = _researcher(tmp_path, [], response=_Resp(200, DDG_HTML))
    res.search("stack research")
    block = res.context_block()
    assert "KANDIDAT" in block
    assert "bukan bukti" in block


def test_report_labels_the_note_as_a_lead(tmp_path):
    res = _researcher(tmp_path, [], response=_Resp(200, DDG_HTML))
    out = res.report(res.search("leads only"))
    assert "status epistemik" in out
    assert "LEAD" in out or "lead" in out


def test_persisted_notes_contain_no_secret_shapes(tmp_path):
    """Notes are written to disk; a key that appeared in a search snippet must
    not travel into storage verbatim."""
    html = '<a class="result__a" href="https://x.test/a">token=SUPERSECRETVALUE1234567890</a>'
    res = _researcher(tmp_path, [], response=_Resp(200, html))
    res.search("secret leak")
    blob = "\n".join(p.read_text() for p in (tmp_path / "research").glob("*.jsonl"))
    assert "SUPERSECRETVALUE1234567890" not in blob


# --------------------------------------------------------------------------- #
# dispatch wiring
# --------------------------------------------------------------------------- #
def test_research_tool_requires_its_arguments():
    from types import SimpleNamespace

    from wth_ai.agents.supervisor_dispatch import _dispatch_research

    reset_researchers()
    ctx = SimpleNamespace(state=SimpleNamespace(session_id="wire"))
    assert "query" in _dispatch_research(ctx, {"action": "search"})
    assert "url" in _dispatch_research(ctx, {"action": "fetch"})
    assert "tidak dikenal" in _dispatch_research(ctx, {"action": "teleport"})


def test_researcher_registry_is_per_session(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    reset_researchers()
    a = get_researcher("session-a")
    b = get_researcher("session-b")
    assert a is not b
    assert get_researcher("session-a") is a

def test_search_falls_through_to_the_next_backend(tmp_path):
    """A network that blocks the first backend must not disable research.

    Measured live: DuckDuckGo (html + lite + api) is unreachable from this
    network while Bing answers in 0.34s. The chain records which backend answered
    so the result's provenance is never implied."""
    calls: list = []
    bing_html = (
        '<ol id="b_results"><li class="b_algo"><h2><a href="https://example.org/a">'
        'Bing result title</a></h2><p>snippet text</p></li></ol>'
    )

    def _get(url: str, *, timeout: int = 25):
        calls.append(url)
        if "duckduckgo" in url:
            raise TimeoutError("blocked by network")
        assert "bing" in url or "brave" in url
        return _Resp(200, bing_html)

    res = Researcher(session_id="s", notes_dir=tmp_path / "r", http_get=_get, delay_seconds=0)
    note = res.search("fallback please")

    assert note.ok, note.error
    assert note.backend == "bing"
    assert "Bing result title" in note.text
    assert len(calls) >= 2, "backend pertama harus dicoba dulu"


def test_all_backends_unreachable_says_so(tmp_path):
    def _get(url: str, *, timeout: int = 25):
        raise TimeoutError("no route")

    res = Researcher(session_id="s", notes_dir=tmp_path / "r", http_get=_get, delay_seconds=0)
    note = res.search("nobody home")
    assert not note.ok
    assert "tidak terjangkau" in note.error
    assert "0 hasil" not in (note.text or "")

# --------------------------------------------------------------------------- #
# Bing result link handling (measured defect, real page shape)
# --------------------------------------------------------------------------- #
_BING_REAL_URL = "https://portswigger.net/web-security/request-smuggling"
_BING_TOKEN = "aHR0cHM6Ly9wb3J0c3dpZ2dlci5uZXQvd2ViLXNlY3VyaXR5L3JlcXVlc3Qtc211Z2dsaW5n"


def test_bing_redirect_urls_are_unwrapped():
    """Every Bing result href is a `bing.com/ck/a?...&u=a1<base64>` wrapper.

    Storing it verbatim puts a redirect blob in the URL field: not the page,
    not fetchable as a source, and shown to the operator as if it were."""
    from wth_ai.research.engine import unwrap_result_url

    wrapped = f"https://www.bing.com/ck/a?!&&p=abc&u=a1{_BING_TOKEN}&ntb=1"
    assert unwrap_result_url(wrapped) == _BING_REAL_URL
    assert unwrap_result_url("https://plain.test/x") == "https://plain.test/x"


def test_bing_parser_prefers_the_h2_link_and_dedupes():
    """Verified live: taking `item.find("a")` produced results titled
    "nextdirect.com" / "next.co.uk" (the display-url line), not the headline."""
    bing_html = """<ol id="b_results">
    <li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&u=a1aHR0cHM6Ly9wb3J0c3dpZ2dlci5uZXQvd2ViLXNlY3VyaXR5L3JlcXVlc3Qtc211Z2dsaW5n&ntb=1">Judul Asli</a></h2>
      <p>snippet asli</p></li>
    <li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&u=a1aHR0cHM6Ly9wb3J0c3dpZ2dlci5uZXQvd2ViLXNlY3VyaXR5L3JlcXVlc3Qtc211Z2dsaW5n&ntb=1">Duplikat</a></h2>
      <p>same target</p></li>
    <li class="b_algo"><a href="https://www.example.com/display">example.com</a>
      <h2><a href="https://real.test/actual">Judul Sebenarnya</a></h2><p>snippet</p></li>
    </ol>""".format(token=_BING_TOKEN)

    results = Researcher._parse_results(bing_html, limit=10, backend="bing")
    urls = [r.url for r in results]
    assert _BING_REAL_URL in urls, urls
    assert "https://real.test/actual" in urls, urls
    assert len(urls) == len(set(urls)), "hasil harus di-dedupe"
    assert all("bing.com/ck" not in u for u in urls), "URL wrapper tidak boleh lolos"
    titles = [r.title for r in results]
    assert "example.com" not in titles, f"baris display-url dianggap judul: {titles}"

# --------------------------------------------------------------------------- #
# Browser path: the field name was wrong, and the failure was invisible
# --------------------------------------------------------------------------- #
def test_session_text_reads_page_text_not_html():
    """Runtime introspection showed `BrowserSession` exposes `page_text`; there
    is no `html`. Reading the wrong attribute made the browser path launch
    chromium, navigate (8.2s, ok=True) and return ZERO characters."""
    from types import SimpleNamespace

    from wth_ai.research.engine import _session_text

    assert _session_text(SimpleNamespace(page_text="real article text")) == "real article text"
    assert _session_text(SimpleNamespace(html="<p>legacy</p>")) == "<p>legacy</p>"
    assert _session_text(SimpleNamespace(page_text="", html="")) == ""
    assert _session_text(SimpleNamespace()) == ""


def test_browser_fetch_reports_an_empty_page_instead_of_succeeding(tmp_path, monkeypatch):
    """`ok=True` with no text is the exact shape that hid the field-name bug, so
    an empty page must be a reported failure."""
    import sys
    from types import SimpleNamespace

    fake_nodriver = SimpleNamespace(__version__="test")
    monkeypatch.setitem(sys.modules, "nodriver", fake_nodriver)

    class _Controller:
        def __init__(self, *a, **k) -> None:
            pass

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        async def navigate(self, url, *, wait_seconds=4.0, wait_for=""):
            return SimpleNamespace(page_text="", title="Empty", cloudflare_detected=False)

    import wth_ai.browser.controller as controller_mod

    monkeypatch.setattr(controller_mod, "BrowserController", _Controller)

    res = Researcher(session_id="s", notes_dir=tmp_path / "r", delay_seconds=0)
    note = res.browser_fetch("https://js.test/page")
    assert not note.ok
    assert "tidak ada teks" in note.error


def test_browser_fetch_keeps_text_and_flags_cloudflare(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(sys.modules, "nodriver", SimpleNamespace())
    import wth_ai.browser.controller as controller_mod

    class _Controller:
        def __init__(self, *a, **k) -> None:
            pass

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        async def navigate(self, url, *, wait_seconds=4.0, wait_for=""):
            return SimpleNamespace(
                page_text="Middleware runs before the request is completed. " * 30,
                title="Middleware docs",
                cloudflare_detected=True,
                cloudflare_bypassed=True,
            )

    monkeypatch.setattr(controller_mod, "BrowserController", _Controller)
    res = Researcher(session_id="s", notes_dir=tmp_path / "r", delay_seconds=0)
    note = res.browser_fetch("https://js.test/page")
    assert note.ok
    assert note.title == "Middleware docs"
    assert "Middleware runs before" in note.text
    assert any("cloudflare" in tag for tag in note.tech_tags)

def test_session_text_prefers_rendered_text_over_truncated_html():
    """Live measurement: 1.27 MB of HTML truncated to 50k yielded 545 chars of
    navigation chrome, while innerText held 23,357 chars of article."""
    from types import SimpleNamespace

    from wth_ai.research.engine import _session_text

    session = SimpleNamespace(
        visible_text="The proxy.js file runs code before a request is completed. " * 40,
        page_text="<html><nav>Docs Learn Blog</nav></html>",
    )
    text = _session_text(session)
    assert "runs code before a request" in text
    assert "<nav>" not in text

    # Falls back to the HTML channel when rendered text is unavailable.
    only_html = SimpleNamespace(page_text="<p>fallback</p>")
    assert _session_text(only_html) == "<p>fallback</p>"


def test_navigate_captures_visible_text(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from wth_ai.browser.controller import BrowserController

    class _Tab:
        async def get_content(self):
            return "<html><body><nav>chrome</nav></body></html>"

        async def evaluate(self, expr):
            assert "innerText" in expr
            return "Rendered article body. " * 50

        async def select(self, selector, timeout=0):
            return SimpleNamespace()

    controller = BrowserController(headless=True)

    async def _noop():
        return None

    controller.start = _noop  # type: ignore[method-assign]
    controller._browser = SimpleNamespace(get=lambda url: _async(_Tab()))  # type: ignore[assignment]
    controller._extract_forms = _noop  # type: ignore[method-assign]
    controller._extract_links = _noop  # type: ignore[method-assign]

    session = asyncio.run(controller.navigate("https://x.test", wait_seconds=0))
    assert "Rendered article body" in session.visible_text
    assert session.page_text.startswith("<html>")


async def _async(value):
    """Awaitable wrapper: `nodriver`-shaped fakes need a coroutine factory."""
    return value
