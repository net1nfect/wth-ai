"""Chain templates: finding berbukti -> probe lanjutan yang konkret."""

from __future__ import annotations

from urllib.parse import unquote_plus

from wth_ai.hunt.chains import chain_next_tests
from wth_ai.hunt.proactive import propose_next_tests


def finding(vuln_class="idor", state="verified", **extra):
    data = {
        "id": f"FIND-{vuln_class}",
        "vuln_class": vuln_class,
        "state": state,
        "title": f"{vuln_class} finding",
        "description": "bukti dari executor",
        "evidence": ["bukti"],
    }
    data.update(extra)
    return data


def pair(url, body="", role="probe"):
    return {"role": role, "method": "GET", "url": url, "body_snippet": body}


def urls(tests):
    return [test.probe["url"] for test in tests]


def test_idor_chain_enumerates_numeric_path():
    tests = chain_next_tests(
        findings=[finding("idor")],
        request_pairs=[pair("http://t/api/user/2", "user2@arena.local")],
    )
    assert {"http://t/api/user/3", "http://t/api/user/1", "http://t/api/user/0"} <= set(urls(tests))
    assert all("chain:idor-enum" in t.probe["purpose"] for t in tests)


def test_idor_chain_uses_query_id_when_path_has_none():
    tests = chain_next_tests(
        findings=[finding("idor")],
        request_pairs=[pair("http://t/object?account=5", "acct5")],
    )
    assert any("account=6" in url for url in urls(tests))
    assert any("account=4" in url for url in urls(tests))


def test_sqli_chain_builds_data_exfil_probes():
    tests = chain_next_tests(
        findings=[finding("sqli")],
        request_pairs=[pair("http://t/sqli?id=1", "sql syntax error")],
    )
    decoded = [unquote_plus(url) for url in urls(tests)]
    assert any("UNION SELECT @@version" in url for url in decoded)
    assert any("LOAD_FILE('/etc/passwd')" in url for url in decoded)
    assert all("id=" in url for url in urls(tests))


def test_ssrf_chain_targets_internal_metadata_and_ports():
    tests = chain_next_tests(
        findings=[finding("ssrf")],
        request_pairs=[pair("http://t/fetch?url=http%3A%2F%2Fexample.com", "remote:ok")],
    )
    joined = " ".join(unquote_plus(url) for url in urls(tests))
    assert "169.254.169.254" in joined
    assert "127.0.0.1:80" in joined
    assert "localhost:8080" in joined


def test_traversal_chain_reads_secret_files():
    tests = chain_next_tests(
        findings=[finding("path_traversal")],
        request_pairs=[pair("http://t/dl?file=notes.txt", "root:x:0:0:")],
    )
    joined = " ".join(unquote_plus(url) for url in urls(tests))
    assert "/etc/hostname" in joined
    assert "/proc/self/environ" in joined


def test_open_redirect_chain_adds_external_and_oidc_probe():
    tests = chain_next_tests(
        findings=[finding("open_redirect")],
        request_pairs=[pair("http://t/login?redirect_uri=%2Fhome", "")],
    )
    joined = " ".join(unquote_plus(url) for url in urls(tests))
    assert "https://wth-redirect.example/x" in joined
    assert "http://t/.well-known/openid-configuration" in joined


def test_auth_chain_probes_admin_surface():
    tests = chain_next_tests(
        findings=[finding("auth")],
        request_pairs=[pair("http://t/api/me", '{"role":"admin"}')],
    )
    assert {"http://t/admin", "http://t/api/admin", "http://t/dashboard"} == set(urls(tests))


def test_chain_skips_false_positive_without_evidence():
    tests = chain_next_tests(
        findings=[finding("idor", state="false_positive")],
        request_pairs=[pair("http://t/api/user/2")],
    )
    assert tests == []


def test_chain_without_url_or_pairs_is_empty():
    assert chain_next_tests(findings=[finding("sqli")], request_pairs=[]) == []
    assert chain_next_tests(findings=[finding("auth")], request_pairs=[]) == []


def test_chain_uses_pair_that_matches_evidence_text():
    tests = chain_next_tests(
        findings=[finding("idor", evidence=["admin@arena.local"])],
        request_pairs=[
            pair("http://t/api/user/1", "admin@arena.local"),
            pair("http://t/api/user/9", "other@arena.local"),
        ],
    )
    assert "http://t/api/user/2" in urls(tests)
    assert "http://t/api/user/8" not in urls(tests)


def test_chain_respects_limit_and_dedupes():
    tests = chain_next_tests(
        findings=[finding("sqli"), finding("sqli")],
        request_pairs=[pair("http://t/sqli?id=1")],
        limit=2,
    )
    assert len(tests) == 2
    assert len(set(urls(tests))) == 2


def test_chain_is_integrated_into_propose_next_tests():
    tests = propose_next_tests(
        findings=[finding("idor")],
        request_pairs=[pair("http://t/api/user/2", "user2@arena.local")],
        limit=12,
    )
    assert any("chain:idor-enum" in str(t.probe.get("purpose", "")) for t in tests)
