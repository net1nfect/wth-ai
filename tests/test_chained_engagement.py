"""Skenario berantai di arena: IDOR -> email admin -> endpoint admin.

Bukan 5 kasus terisolasi: langkah kedua memakai data yang didapat dari langkah
pertama. State mencatat rantai, verifier deterministik memutuskan tiap langkah.
"""

from __future__ import annotations

import time

import requests

from wth_ai.agents.verifier import VerifierAgent
from wth_ai.arena.targets import ArenaServer
from wth_ai.models import Finding, FindingState
from wth_ai.state import EngagementState, endpoint_key, observation_from_result


def _fetch(state: EngagementState, url: str, role: str, tid: str) -> tuple[object, str]:
    started = time.perf_counter()
    response = requests.get(url, timeout=5)
    elapsed_ms = (time.perf_counter() - started) * 1000
    observation = observation_from_result(
        {
            "method": "GET",
            "url": url,
            "status_code": response.status_code,
            "stdout": response.text,
            "response_headers": dict(response.headers),
            "transcript_id": tid,
            "elapsed_ms": elapsed_ms,
        },
        role=role,
    )
    state.observe(observation)
    return observation, response.text


def _finding(title: str) -> Finding:
    return Finding(
        id="FIND-chain",
        title=title,
        severity="high",
        state=FindingState.CANDIDATE,
        component="arena",
        affected_asset="arena",
    )


def test_chained_idor_to_admin_export():
    arena = ArenaServer()
    state = EngagementState(target=arena.base)
    try:
        # Langkah 1: baseline adalah objek milik "user 2"; probe menukar id ke 1.
        baseline, _ = _fetch(state, f"{arena.base}/api/user/2", "baseline", "TX-1")
        probe, probe_body = _fetch(state, f"{arena.base}/api/user/1", "probe", "TX-2")
        assert "admin@arena.local" in probe_body

        idor = state.add_hypothesis(
            "idor",
            endpoint_key(probe.url),
            "identifier user lain mengembalikan objek admin",
            posterior=0.82,
        )
        agent = VerifierAgent.__new__(VerifierAgent)
        result = agent.verify_offline(
            _finding("IDOR /api/user/{id}"),
            pairs=[baseline.to_pair(), probe.to_pair()],
            vuln_class="idor",
        )
        assert result["verdict"] == "verified", result
        state.record_verdict(
            idor.id,
            "verified",
            reasons=result["reason"].split("; "),
            evidence_refs=result["evidence_refs"],
            finding_summary="objek admin terbaca lintas user",
        )

        # Langkah 2: email admin dari langkah 1 dipakai sebagai aktor.
        guest, _ = _fetch(
            state, f"{arena.base}/admin/export?actor=guest@arena.local", "baseline", "TX-3"
        )
        exported, export_body = _fetch(
            state, f"{arena.base}/admin/export?actor=admin@arena.local", "probe", "TX-4"
        )
        assert "ARENA-ADMIN-EXPORT" in export_body

        authz = state.add_hypothesis(
            "auth",
            endpoint_key(exported.url),
            "aktor dari parameter dipercaya; email admin bocor dari IDOR",
            posterior=0.77,
            parent_id=idor.id,
        )
        result2 = agent.verify_offline(
            _finding("Broken access control /admin/export"),
            pairs=[guest.to_pair(), exported.to_pair()],
            vuln_class="auth",
        )
        assert result2["verdict"] == "verified", result2
        state.record_verdict(
            authz.id,
            "verified",
            reasons=result2["reason"].split("; "),
            evidence_refs=result2["evidence_refs"],
            finding_summary="export admin terbaca dengan aktor hasil IDOR",
        )

        metrics = state.metrics()
        assert metrics["verified"] == 2
        assert metrics["chain_depth"] == 2
        assert metrics["turns_to_first_evidence"] == 2
        assert metrics["wasted_probes"] == 0
        context = state.context()
        assert "Terverifikasi" in context
        assert "chain_depth=2" in context
    finally:
        arena.stop()


def test_chained_state_survives_reload(tmp_path):
    arena = ArenaServer()
    state = EngagementState(target=arena.base, engagement_id="ENG-chain")
    try:
        base, _ = _fetch(state, f"{arena.base}/api/user/2", "baseline", "TX-1")
        probe, _ = _fetch(state, f"{arena.base}/api/user/1", "probe", "TX-2")
        hyp = state.add_hypothesis("idor", "/api/user/{id}", "cross-user", posterior=0.8)
        state.record_verdict(hyp.id, "verified", reasons=["objek berbeda"], evidence_refs=["TX-2"])
        state.save(tmp_path)

        loaded = EngagementState.load("ENG-chain", tmp_path)
        assert loaded.chain_depth == state.chain_depth == 1
        assert loaded.metrics() == state.metrics()
        # Baseline hasil reload tetap membandingkan probe baru ke observasi awal.
        changes = loaded.observe(
            observation_from_result(
                {
                    "method": "GET",
                    "url": f"{arena.base}/api/user/1",
                    "status_code": 500,
                    "stdout": "error baru",
                    "transcript_id": "TX-9",
                },
                role="probe",
            )
        )
        assert changes
        assert loaded.anomalies
    finally:
        arena.stop()


def test_chain_template_proposal_is_executed_and_observed():
    """Loop penuh: bukti IDOR → usulan rantai → request nyata → observasi baru."""
    from wth_ai.hunt.proactive import propose_next_tests
    from wth_ai.state import observation_from_result

    arena = ArenaServer()
    state = EngagementState(target=arena.base)
    try:
        baseline, _ = _fetch(state, f"{arena.base}/api/user/2", "baseline", "TX-1")
        probe, probe_body = _fetch(state, f"{arena.base}/api/user/1", "probe", "TX-2")
        finding = {
            "id": "FIND-chain",
            "vuln_class": "idor",
            "state": "verified",
            "title": "IDOR /api/user/{id}",
            "description": probe_body,
            "evidence": [probe_body[:60]],
        }
        proposals = propose_next_tests(
            findings=[finding],
            request_pairs=[baseline.to_pair(), probe.to_pair()],
            limit=12,
        )
        chain = [p for p in proposals if str(p.probe.get("purpose", "")).startswith("chain:idor")]
        assert chain, [p.to_dict() for p in proposals]
        target = chain[0].probe["url"]
        response = requests.get(target, timeout=5)
        assert response.status_code == 200
        observation = observation_from_result(
            {
                "method": "GET",
                "url": target,
                "status_code": response.status_code,
                "stdout": response.text,
                "response_headers": dict(response.headers),
                "transcript_id": "TX-chain",
            },
            role="probe",
        )
        state.observe(observation)
        assert state.metrics()["probes"] == 3
        assert state.anomalies, "objek hasil chain harus terdeteksi sebagai delta"
    finally:
        arena.stop()
