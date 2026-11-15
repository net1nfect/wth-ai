"""GAP-010: verifikasi deterministik tanda bukti konkret, tanpa LLM."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from wth_ai.verify.deterministic import (
    normalize_class,
    verify_finding,
    verify_pair,
)


def pair(status=200, body="", *, role="", headers=None, elapsed_ms=0.0, tid="TX"):
    return {
        "role": role,
        "response_status": status,
        "body_snippet": body,
        "response_headers": headers or {},
        "elapsed_ms": elapsed_ms,
        "transcript_id": tid,
    }


def finding(title="", evidence=()):
    return SimpleNamespace(
        id="FIND-1",
        title=title,
        description="",
        impact="",
        evidence=list(evidence),
    )


@pytest.mark.parametrize(
    ("klass", "base", "probe", "expected", "verdict"),
    [
        (
            "sqli",
            pair(200, "baseline ok " + "a" * 100),
            pair(500, "you have an error in your sql syntax near '''"),
            (),
            "verified",
        ),
        (
            "sqli",
            pair(200, "ok", elapsed_ms=20),
            pair(200, "ok", elapsed_ms=5200),
            (),
            "verified",
        ),
        ("sqli", pair(200, "same"), pair(200, "same"), (), "needs_more_evidence"),
        (
            "xss",
            pair(200, "<div>hasil: aman</div>"),
            pair(200, "<div>hasil: <script>alert(1)</script></div>"),
            (),
            "verified",
        ),
        (
            "xss",
            pair(200, "<div>aman</div>"),
            pair(200, "<div>&lt;script&gt;alert(1)&lt;/script&gt;</div>"),
            (),
            "false_positive",
        ),
        (
            "ssti",
            pair(200, "hello arena"),
            pair(200, "hello 49"),
            ("49",),
            "verified",
        ),
        (
            "ssti",
            pair(200, "hello arena"),
            pair(200, "hello {{7*7}}"),
            (),
            "false_positive",
        ),
        (
            "cmdi",
            pair(200, "lookup ok"),
            pair(200, "uid=1000(arena) gid=1000(arena) groups=1000(arena)"),
            (),
            "verified",
        ),
        (
            "path_traversal",
            pair(404, "not found"),
            pair(200, "root:x:0:0:root:/root:/bin/bash"),
            (),
            "verified",
        ),
        (
            "idor",
            pair(200, '{"id": 2, "email": "user2@arena.local", "role": "user"}'),
            pair(200, '{"id": 1, "email": "admin@arena.local", "role": "admin"}'),
            (),
            "verified",
        ),
        (
            "idor",
            pair(200, '{"id": 2, "email": "user2@arena.local"}'),
            pair(200, '{"id": 2, "email": "user2@arena.local"}'),
            (),
            "false_positive",
        ),
        ("idor", pair(200, "{}"), pair(403, "forbidden"), (), "false_positive"),
        ("auth", pair(401, "unauthorized"), pair(200, "welcome admin"), (), "verified"),
        ("auth", pair(401, "unauthorized"), pair(401, "unauthorized"), (), "false_positive"),
        (
            "ssrf",
            pair(400, "missing url param"),
            pair(200, "remote:health page arena-selftest-marker"),
            ("arena-selftest-marker",),
            "verified",
        ),
        ("ssrf", pair(400, "missing"), pair(200, "remote:something"), (), "needs_more_evidence"),
        (
            "open_redirect",
            pair(200, "ok"),
            pair(302, "", headers={"location": "https://evil.example/x"}),
            (),
            "verified",
        ),
        (
            "open_redirect",
            pair(200, "ok"),
            pair(302, "", headers={"location": "/local"}),
            (),
            "needs_more_evidence",
        ),
    ],
)
def test_verify_pair_rules(klass, base, probe, expected, verdict):
    result = verify_pair(klass, base, probe, expected=expected)
    assert result.verdict == verdict, result.reasons
    if verdict == "verified":
        assert result.confidence >= 0.6
        assert result.reasons


def test_verify_pair_never_claims_verified_without_signal():
    result = verify_pair("generic", pair(200, "same"), pair(200, "same"))
    assert result.verdict == "needs_more_evidence"
    assert result.confidence < 0.6


def test_verify_pair_sqli_identical_is_not_false_positive():
    # Tidak ada sinyal bukan berarti aman; jangan klaim false_positive.
    result = verify_pair("sqli", pair(200, "same"), pair(200, "same"))
    assert result.verdict == "needs_more_evidence"


def test_normalize_class_aliases():
    assert normalize_class("sql_injection") == "sqli"
    assert normalize_class(None, "IDOR on /api/user") == "idor"
    assert normalize_class(None, "command injection uid=") == "cmdi"
    assert normalize_class("auth_jwt") == "auth"


def test_verify_finding_requires_two_observations():
    assert verify_finding(finding("sqli"), [pair(500, "sql syntax error")]) is None


def test_verify_finding_uses_roles_not_order():
    pairs = [
        pair(500, "sql syntax error", role="probe"),
        pair(200, "ok", role="baseline"),
    ]
    result = verify_finding(finding("sqli error"), pairs)
    assert result is not None
    assert result["verdict"] == "verified"
    assert result["vuln_class"] == "sqli"
    assert result["model"] == "deterministic"


def test_verify_finding_infers_class_from_title():
    pairs = [pair(200, "a"), pair(200, "uid=0(root) gid=0(root)")]
    result = verify_finding(finding("command injection via host"), pairs)
    assert result is not None
    assert result["verdict"] == "verified"
    assert result["vuln_class"] == "cmdi"


def test_finding_carries_vuln_class_and_task_id():
    from wth_ai.findings_db import FindingsDB

    finding = FindingsDB().add_finding({
        "title": "SQLi via id",
        "vuln_class": "sqli",
        "task_id": "TASK-9",
    })
    assert finding.vuln_class == "sqli"
    assert finding.task_id == "TASK-9"
    assert finding.to_dict()["task_id"] == "TASK-9"


def _agent(client_available: bool):
    from wth_ai.agents.verifier import VerifierAgent

    agent = VerifierAgent.__new__(VerifierAgent)
    agent.client = SimpleNamespace(available=lambda: client_available)
    return agent


def _db_finding(vuln_class: str = "sqli"):
    from wth_ai.findings_db import FindingsDB

    return FindingsDB().add_finding({"title": "finding", "vuln_class": vuln_class})


def test_verify_with_evidence_verified_never_reaches_llm():
    from wth_ai.models import SessionState

    agent = _agent(True)

    def _boom(*args, **kwargs):
        raise AssertionError("LLM tidak boleh dipanggil saat bukti deterministik memutuskan")

    agent.verify = _boom
    result = agent.verify_with_evidence(
        _db_finding("sqli"),
        SessionState(),
        pairs=[pair(200, "ok"), pair(500, "you have an error in your sql syntax")],
    )
    assert result["verdict"] == "verified"
    assert result["model"] == "deterministic"


def test_verify_with_evidence_falls_back_to_llm_only_when_unresolved():
    from wth_ai.models import SessionState

    agent = _agent(True)
    called = {"n": 0}

    def _llm(finding, state, **kwargs):
        called["n"] += 1
        return {"verdict": "needs_more_evidence", "model": "llm"}

    agent.verify = _llm
    result = agent.verify_with_evidence(
        _db_finding("sqli"),
        SessionState(),
        pairs=[pair(200, "same"), pair(200, "same")],
    )
    assert called["n"] == 1
    assert result["model"] == "llm"


def test_verify_with_evidence_offline_fallback_without_llm():
    from wth_ai.models import SessionState

    agent = _agent(False)
    result = agent.verify_with_evidence(
        _db_finding("sqli"),
        SessionState(),
        pairs=[pair(200, "same"), pair(200, "same")],
    )
    assert result["model"] == "deterministic"
    assert result["verdict"] == "needs_more_evidence"
