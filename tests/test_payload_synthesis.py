"""Sintesis payload dari fakta respons — tanpa jaringan dan tanpa LLM wajib."""

from __future__ import annotations

from urllib.parse import unquote

from wth_ai.hunt.synthesis import (
    ServerProfile,
    synthesize_next_tests,
    synthesize_payloads,
    synthesize_with_llm,
)


def _pair(**overrides):
    base = {
        "url": "http://target.test/item?id=1",
        "method": "GET",
        "response_status": 200,
        "body_snippet": "",
        "response_headers": {"Content-Type": "text/html"},
    }
    base.update(overrides)
    return base


def test_profile_detects_mysql_error_and_param():
    profile = ServerProfile.from_pair(_pair(
        response_status=500,
        body_snippet="You have an error in your SQL syntax near 'id=1'",
    ))
    assert profile.dbms == "mysql"
    assert profile.param == "id"
    assert profile.status == 500


def test_profile_detects_reflection_context_in_double_quote_attribute():
    marker = "wthXSSprobe9z8y7"
    profile = ServerProfile.from_pair(_pair(
        body_snippet=f'<input class="{marker}" value="x">',
    ), marker=marker)
    assert profile.reflection_context == "attr_dq"
    assert profile.reflected_marker == marker


def test_sqli_synthesis_uses_detected_dialect_and_real_endpoint():
    profile = ServerProfile.from_pair(_pair(
        response_status=500,
        body_snippet="You have an error in your SQL syntax",
    ))
    probes = synthesize_payloads(profile, "sqli", limit=3)
    assert probes
    assert any("SLEEP" in str(p.probe.get("url", "")) for p in probes)
    assert all("target.test/item" in str(p.probe.get("url", "")) for p in probes)
    assert all(p.vuln_class == "sqli" for p in probes)


def test_xss_breakout_follows_reflection_context():
    marker = "wthXSSprobeABCD"
    profile = ServerProfile.from_pair(_pair(
        body_snippet=f'<div class="{marker}">',
    ), marker=marker)
    probes = synthesize_payloads(profile, "xss", limit=2)
    assert probes
    assert any("onfocus" in str(p.probe.get("url", "")) for p in probes)
    assert all("wth" in str(p.probe.get("url", "")) for p in probes)


def test_traversal_depth_uses_path_depth():
    profile = ServerProfile.from_pair(_pair(
        url="http://target.test/a/b/c?file=index",
        body_snippet="ok",
    ))
    probes = synthesize_payloads(profile, "path_traversal", limit=1)
    assert probes
    assert "../../../etc/passwd" in unquote(str(probes[0].probe.get("url", "")))


def test_cmdi_uses_windows_command_when_os_hint_windows():
    profile = ServerProfile.from_pair(_pair(
        body_snippet="Microsoft-IIS/10.0 Windows NT",
        response_headers={"Server": "Microsoft-IIS/10.0"},
    ))
    probes = synthesize_payloads(profile, "cmdi", limit=1)
    assert probes
    assert "whoami" in str(probes[0].probe.get("url", ""))
    assert probes[0].expected == ("windows_user",)


def test_nosql_json_body_preserves_other_fields():
    profile = ServerProfile.from_pair(_pair(
        url="http://target.test/api/login",
        method="POST",
        body='{"user":"admin","pass":"secret"}',
        request_headers={"Content-Type": "application/json"},
        response_headers={"Content-Type": "application/json"},
    ))
    probes = synthesize_payloads(profile, "nosql", limit=1)
    assert probes
    body = str(probes[0].probe.get("body", ""))
    assert '"pass": "secret"' in body
    assert '"$ne": null' in body


def test_xxe_synthesis_posts_xml_entity():
    profile = ServerProfile.from_pair(_pair(
        url="http://target.test/api/xml",
        method="POST",
        response_headers={"Content-Type": "application/xml"},
    ))
    probes = synthesize_payloads(profile, "xxe", limit=1)
    assert probes
    assert probes[0].probe["method"] == "POST"
    assert "file:///etc/passwd" in str(probes[0].probe["body"])


def test_synthesize_next_tests_from_response_signal():
    tests = synthesize_next_tests(request_pairs=[_pair(
        response_status=500,
        body_snippet="You have an error in your SQL syntax",
    )], limit=4)
    assert tests
    assert any(t.vuln_class == "sqli" for t in tests)
    assert all(t.probe.get("url") for t in tests)


class _Router:
    def chat_for_role(self, role, messages, *, timeout=90):
        return (
            '[{"vuln_class":"sqli","title":"LLM dialect probe",'
            '"proves":"error-based SQLi","cleanup":"read-only",'
            '"blast_radius":"self","probe":{"method":"GET",'
            '"url":"http://target.test/item?id=%27"}}]',
            "fake-model",
        )


def test_synthesize_with_llm_includes_profile_and_validates():
    profile = ServerProfile.from_pair(_pair(
        response_status=500,
        body_snippet="You have an error in your SQL syntax",
    ))
    probes = synthesize_with_llm(_Router(), profile, "sqli", limit=1)
    assert probes
    assert probes[0].context == "llm"
    assert "error-based" in probes[0].rationale
    assert "target.test" in str(probes[0].probe.get("url", ""))


def test_proactive_planner_includes_response_synthesis():
    from wth_ai.hunt.proactive import propose_next_tests

    tests = propose_next_tests(
        request_pairs=[_pair(
            response_status=500,
            body_snippet="You have an error in your SQL syntax near '1'",
        )],
        limit=8,
    )
    assert any(t.vuln_class == "sqli" for t in tests)
