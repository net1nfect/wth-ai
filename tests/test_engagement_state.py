"""State engagement + normalizer: delta terstruktur, bukan riwayat chat."""

from __future__ import annotations

from wth_ai.state import (
    EngagementState,
    Observation,
    diff_observations,
    endpoint_key,
    observation_from_result,
    salient_headers,
)


def result(status=200, body="ok", url="http://t/api/user/1", headers=None, tid="TX-1", elapsed=0.0):
    return {
        "method": "GET",
        "url": url,
        "status_code": status,
        "stdout": body,
        "response_headers": headers or {},
        "transcript_id": tid,
        "elapsed_ms": elapsed,
    }


def obs(status=200, body="ok", url="http://t/api/user/1", role="", tid="TX-1", headers=None):
    return observation_from_result(result(status, body, url, headers, tid), role=role)


def test_endpoint_key_normalizes_ids():
    # Host dipertahankan supaya dua virtual host tidak saling menimpa baseline.
    assert endpoint_key("http://t/api/user/1?x=1") == "http://t/api/user/{id}"
    assert endpoint_key("http://t/api/user/550e8400-e29b-41d4-a716-446655440000") == "http://t/api/user/{id}"
    assert endpoint_key("http://t/static/app.js") == "http://t/static/app.js"


def test_salient_headers_hides_cookie_values():
    headers = salient_headers({
        "Server": "arena",
        "Set-Cookie": "session=secret; Path=/; HttpOnly",
        "X-Powered-By": "Flask",
        "X-Other": "not-salient",
    })
    assert headers["server"] == "arena"
    assert headers["set-cookie-names"] == "session"
    assert "secret" not in str(headers)
    assert "x-other" not in headers


def test_observation_keeps_only_snippet_and_fingerprint():
    long_body = "A" * 5000
    observation = obs(body=long_body)
    assert observation.body_length == 5000
    assert len(observation.snippet) == 300
    assert observation.body_sha
    assert long_body not in str(observation.to_dict())


def test_diff_observations_reports_real_changes_only():
    base = obs(body="same", headers={"server": "arena"})
    same = obs(body="same", headers={"server": "arena"})
    assert diff_observations(base, same) == []
    changed = obs(
        status=500,
        body="different body",
        headers={"server": "arena", "www-authenticate": "Basic"},
    )
    changes = diff_observations(base, changed)
    assert any("status 200 -> 500" in item for item in changes)
    assert any("body fingerprint" in item for item in changes)
    assert any("www-authenticate" in item for item in changes)


def test_state_records_anomaly_and_wasted_probe():
    state = EngagementState(target="http://t")
    state.observe(obs(body="baseline", role="baseline"))
    changes = state.observe(obs(status=500, body="error", role="probe", tid="TX-2"))
    assert changes
    assert state.anomalies
    state.observe(obs(status=500, body="error", role="probe", tid="TX-2b"))
    assert state.counters["wasted"] == 0
    state.observe(obs(body="baseline", role="probe", tid="TX-3"))
    assert state.counters["wasted"] == 1


def test_state_chain_depth_counts_linked_verified_hypotheses():
    state = EngagementState(target="http://t")
    first = state.add_hypothesis("idor", "/api/user/{id}", "cross-user object read", posterior=0.8)
    state.record_verdict(first.id, "verified", reasons=["email berubah"], evidence_refs=["EV-1"])
    second = state.add_hypothesis(
        "auth", "/admin/export", "actor parameter trust", posterior=0.7, parent_id=first.id
    )
    state.record_verdict(second.id, "verified", reasons=["403 -> 200"], evidence_refs=["EV-2"])
    assert state.chain_depth == 2
    assert state.counters["verified"] == 2
    assert state.turns_to_first_evidence == 0  # belum ada observasi pada state ini
    assert [f["vuln_class"] for f in state.verified_findings] == ["idor", "auth"]


def test_state_metrics_and_context_are_compact():
    state = EngagementState(target="http://t")
    state.observe(obs(body="baseline", role="baseline"))
    hyp = state.add_hypothesis("xss", "/search", "reflection tanpa escape", posterior=0.66)
    context = state.context()
    assert "probes=1" in context
    assert hyp.id in context
    assert "Hipotesis terbuka" in context
    metrics = state.metrics()
    assert metrics["hypotheses_open"] == 1
    assert metrics["chain_depth"] == 0


def test_state_persist_and_load_roundtrip(tmp_path):
    state = EngagementState(target="http://t", engagement_id="ENG-test")
    state.observe(obs(body="baseline", role="baseline"))
    state.observe(obs(status=500, body="sql syntax error", role="probe", tid="TX-2"))
    hyp = state.add_hypothesis("sqli", "/sqli", "error SQL", posterior=0.9)
    state.record_verdict(hyp.id, "verified", reasons=["error SQL"], evidence_refs=["EV-9"])
    path = state.save(tmp_path)
    assert path.exists()

    loaded = EngagementState.load("ENG-test", tmp_path)
    assert loaded.metrics() == state.metrics()
    assert loaded.verified_findings == state.verified_findings
    # baseline ikut pulih: probe berikutnya masih dibandingkan ke baseline awal.
    changes = loaded.observe(obs(status=500, body="another error", role="probe", tid="TX-3"))
    assert changes


def test_observation_from_observation_preserves_fields():
    original = obs(status=201, body="created", role="probe", tid="TX-7")
    clone = Observation.from_dict(original.to_dict())
    assert clone == original


def test_autopilot_feeds_engagement_state():
    from wth_ai.hunt.autopilot import run_autopilot

    class Result:
        status_code = 200
        transcript_id = "TX-auto"
        stdout = "body"
        response_headers = {"Server": "arena"}

    class SessionStub:
        metadata: dict = {}
        scope = type("Scope", (), {"destructive_allowed": False, "target": "http://t"})()

    engagement = EngagementState(target="http://t")
    report = run_autopilot(
        target="http://t",
        run_probe=lambda probe: Result(),
        state=SessionStub(),
        max_cycles=1,
        max_steps_per_cycle=1,
        adaptive=False,
        vuln_class="injection_detect",
        engagement=engagement,
    )
    assert report.ran >= 1
    assert engagement.metrics()["probes"] == report.ran
    assert len(engagement.observations) == report.ran
    assert engagement.observations[0].headers.get("server") == "arena"
