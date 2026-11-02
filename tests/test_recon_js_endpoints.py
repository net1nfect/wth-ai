"""Regression tests for JS-bundle endpoint extraction (priority-list pollution).

Measured failure (real target `chat.heroflow.my.id`): the recon report's
"Top Priority Targets" began with

    http://www.w3.org/2000/svg      (an XML namespace)
    /./                             (a JavaScript path fixture)
    /a/b, /a/i                      (fixtures)
    https://github.com/zloirock/core-js   (a library homepage)
    https://тест                    (non-ASCII)

every one of them labelled `endpoint_kind="api"` and therefore scored
`exposed-api (+3)` by `scoring.py`, outranking the single genuine target. The
extractor emitted every quoted path-like string in the bundle as an API
endpoint of the target.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.recon import js

# The exact strings observed in the live bundle, in a bundle-shaped fixture.
_LIVE_JUNK = """
var NS = "http://www.w3.org/2000/svg";
var home = "https://github.com/zloirock/core-js";
var lic = "https://github.com/zloirock/core-js/blob/v3.38.1/LICENSE";
var a = "/a/b"; var b = "/a/i"; var c = "/./"; var d = "https://тест";
var enc = "https://a/c%20d";
var real = "/api/v1/users";
var gql = "/graphql";
var docs = "/swagger/index.html";
var page = "/_not-found";
var own = "https://chat.heroflow.my.id/api/v1/chat";
"""


def test_offsite_urls_from_the_bundle_are_not_target_endpoints():
    found = js.extract_endpoints(_LIVE_JUNK, base_host="chat.heroflow.my.id")
    for offsite in (
        "http://www.w3.org/2000/svg",
        "https://github.com/zloirock/core-js",
        "https://github.com/zloirock/core-js/blob/v3.38.1/LICENSE",
    ):
        assert offsite not in found, f"{offsite} masih dianggap endpoint target"


def test_own_host_and_subdomains_are_kept():
    found = js.extract_endpoints(_LIVE_JUNK, base_host="chat.heroflow.my.id")
    assert "https://chat.heroflow.my.id/api/v1/chat" in found

    other = js.extract_endpoints(
        '"/api/x" "https://api.heroflow.my.id/v1/y"', base_host="chat.heroflow.my.id"
    )
    assert "https://api.heroflow.my.id/v1/y" in other, "subdomain satu registrable harus lolos"


def test_junk_paths_are_dropped():
    found = js.extract_endpoints(_LIVE_JUNK, base_host="chat.heroflow.my.id")
    assert "/./" not in found
    assert "https://тест" not in found
    assert "https://a/c%20d" not in found


def test_classifier_no_longer_labels_everything_api():
    assert js.classify_endpoint("/api/v1/users") == "api"
    assert js.classify_endpoint("/graphql") == "graphql"
    assert js.classify_endpoint("/swagger/index.html") == "docs"
    assert js.classify_endpoint("/_not-found") == "web"
    assert js.classify_endpoint("/a/b") == "web"


def test_analyze_emits_web_kind_for_plain_page_paths():
    items = js.analyze("https://chat.heroflow.my.id/_next/static/chunks/app.js", _LIVE_JUNK)
    kinds = {
        it["url"]: it["endpoint_kind"]
        for it in items if it.get("kind") == "endpoint"
    }
    assert kinds.get("/api/v1/users") == "api"
    assert kinds.get("/graphql") == "graphql"
    assert kinds.get("/_not-found") == "web", "path biasa tidak boleh jadi 'api'"
    # The off-site URLs must not appear as endpoints at all.
    assert not any("github.com" in url for url in kinds)
    assert not any("w3.org" in url for url in kinds)


def test_scoring_does_not_promote_plain_paths_as_exposed_api():
    """`exposed-api` (+3) is reserved for kind in docs/graphql/api."""
    from wth_ai.recon.js import analyze as js_analyze
    from wth_ai.recon.normalizer import ReconNormalizer
    from wth_ai.recon.scoring import score

    norm = ReconNormalizer()
    for item in js_analyze(
        "https://chat.heroflow.my.id/_next/static/chunks/app.js", _LIVE_JUNK
    ):
        norm.add_item("builtin-js", item)

    targets = {t.asset: t for t in score(norm)}
    page = targets.get("/_not-found")
    if page is not None:
        assert not any(r.startswith("exposed-") for r in page.reasons), page.reasons

    # Nothing probed `/api/v1/users`, so it must NOT be labelled "exposed" yet.
    # (This assertion previously expected `exposed-api` -- that expectation was
    # the overclaim itself: the label came from a path pattern in a bundle,
    # while `/openapi.json` on the live target returned 404.)
    api_target = targets.get("/api/v1/users")
    if api_target is not None:
        assert not any(r == "exposed-api" for r in api_target.reasons), api_target.reasons
        assert any("unverified" in r for r in api_target.reasons), api_target.reasons


def test_extract_endpoints_without_base_host_keeps_previous_behaviour():
    """Callers with no target context must not silently lose absolute URLs."""
    found = js.extract_endpoints('"/api/x" "https://example.org/api/y"')
    assert "/api/x" in found
    assert "https://example.org/api/y" in found

# --------------------------------------------------------------------------- #
# "exposed-*" must be verified before it is claimed
# --------------------------------------------------------------------------- #
def test_unprobed_js_path_is_not_labelled_exposed():
    """Live proof of the overclaim: `/openapi.json` scored `exposed-docs (+3)`
    while the real response is a 404 HTML page. A path pattern in a bundle is a
    lead, not a confirmed exposed document."""
    from wth_ai.recon.js import analyze as js_analyze
    from wth_ai.recon.normalizer import ReconNormalizer
    from wth_ai.recon.scoring import score

    norm = ReconNormalizer()
    for item in js_analyze(
        "https://chat.heroflow.my.id/_next/static/chunks/app.js",
        '"/openapi.json" "/graphql" "/api/v1/users"',
    ):
        norm.add_item("builtin-js", item)

    targets = {t.asset: t for t in score(norm)}
    api = targets["/api/v1/users"]
    assert not any(r.startswith("exposed-") for r in api.reasons), api.reasons
    assert any("unverified" in r for r in api.reasons), api.reasons


def test_probed_live_endpoint_still_earns_exposed_label():
    from wth_ai.recon.js import analyze as js_analyze
    from wth_ai.recon.normalizer import ReconNormalizer
    from wth_ai.recon.scoring import score

    norm = ReconNormalizer()
    for item in js_analyze(
        "https://chat.heroflow.my.id/_next/static/chunks/app.js", '"/api/v1/users"'
    ):
        norm.add_item("builtin-js", item)
    # Simulate the probe that confirms it serves a document.
    norm.add_item("builtin-probe", {
        "kind": "http_probe", "url": "/api/v1/users", "status": 200,
    })

    targets = {t.asset: t for t in score(norm)}
    api = targets["/api/v1/users"]
    assert any(r == "exposed-api" for r in api.reasons), api.reasons

# --------------------------------------------------------------------------- #
# Coverage: the extractor must SEE modern bundles (loosened, not just filtered)
# --------------------------------------------------------------------------- #
def test_template_literal_routes_are_extracted():
    """The old char class excluded `$`/`{}`, so `/api/users/${id}/posts` -- the
    most common way a modern bundle carries a route -- was invisible."""
    found = js.extract_endpoints('`/api/users/${id}/posts` `/api/files/${fileId}/download`')
    assert "/api/users/{param}/posts" in found
    assert "/api/files/{param}/download" in found


def test_template_routes_are_classified_as_api():
    assert js.classify_endpoint("/api/users/{param}/posts") == "api"


def test_query_strings_are_kept():
    found = js.extract_endpoints('"/api/search?q=1&page=2"')
    assert "/api/search?q=1&page=2" in found


def test_un_normalised_template_expression_is_still_rejected():
    """A raw `${` means the expression was never substituted -- junk, not a route."""
    assert not js._is_plausible_endpoint("/api/${", "example.com")

