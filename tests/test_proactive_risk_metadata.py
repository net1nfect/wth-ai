"""Metadata risiko terstruktur pada saran proaktif (rekomendasi 4).

Sebelum ini `NextTest.risk` hanya string untuk dibaca manusia — tidak bisa
dipakai memfilter kandidat, memutuskan approval, atau memilih jalur eksekusi.
Metadata di bawah diturunkan dari METHOD (fakta HTTP) plus sifat operasi yang
eksplisit, sehingga saran POST yang mengubah state TIDAK bisa lolos sebagai
langkah berisiko rendah.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.hunt.autopilot import _next_to_step
from wth_ai.hunt.proactive import NextTest, derive_risk

URL = "https://example.com/api/cart"


def _test(method: str, **kw) -> NextTest:
    return NextTest(title="t", rationale="r",
                    probe={"method": method, "url": URL}, **kw)


# ---------------------------------------------------------------------------
# derive_risk
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_are_not_mutating(method: str):
    r = derive_risk(method)
    assert r["mutating"] is False
    assert r["destructive"] is False
    assert r["blast_radius"] == "none"


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_post_is_mutating_and_destructive_by_default(method: str):
    """Reversibility tidak diketahui => konservatif destruktif."""
    r = derive_risk(method)
    assert r["mutating"] is True
    assert r["destructive"] is True


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_idempotent_methods_are_mutating_but_not_destructive(method: str):
    r = derive_risk(method)
    assert r["mutating"] is True
    assert r["idempotent"] is True
    assert r["destructive"] is False


def test_reversible_self_scoped_operation_is_not_destructive():
    r = derive_risk("POST", reversible=True, blast_radius="self")
    assert r["destructive"] is False


def test_irreversible_operation_is_destructive():
    r = derive_risk("POST", reversible=False)
    assert r["destructive"] is True


def test_mass_blast_radius_is_flagged():
    r = derive_risk("POST", reversible=True, blast_radius="mass")
    assert r["blast_radius"] == "mass"
    assert r["destructive"] is True


# ---------------------------------------------------------------------------
# NextTest
# ---------------------------------------------------------------------------


def test_post_suggestion_is_never_low_risk():
    t = _test("POST")
    assert t.destructive is True
    assert t.risk != "low"


def test_get_suggestion_stays_low_risk():
    t = _test("GET")
    assert t.destructive is False
    assert t.mutating is False


def test_to_dict_is_json_serializable_and_complete():
    import json

    d = _test("POST").to_dict()
    json.dumps(d)
    for key in ("mutating", "idempotent", "reversible", "blast_radius",
                "requires_auth", "destructive"):
        assert key in d, f"metadata {key} hilang dari serialisasi"


def test_explicit_reversibility_is_respected():
    t = _test("POST", reversible=False)
    assert t.reversible is False and t.destructive is True


# ---------------------------------------------------------------------------
# integrasi dengan autopilot
# ---------------------------------------------------------------------------


def test_next_test_step_carries_destructive_flag():
    step = _next_to_step(_test("POST"))
    assert step.probe.get("destructive") is True
    assert step.probe.get("risk") != "low"


def test_get_next_test_step_is_not_destructive():
    step = _next_to_step(_test("GET"))
    assert step.probe.get("destructive") is False


def test_proactive_mutating_step_is_gated_by_destructive_rule():
    """Saran POST yang mengubah state harus masuk jalur langkah destruktif:
    checkpoint INFLIGHT wajib sukses SEBELUM dikirim."""
    from wth_ai.hunt.autopilot import run_autopilot
    from wth_ai.jobs.checkpoint import StepOutcome

    calls: list[dict] = []

    class _Resp:
        status_code = 200
        stdout = "ok"
        transcript_id = "TX"
        response_headers: dict = {}

    def probe(p: dict) -> _Resp:
        calls.append(dict(p))
        return _Resp()

    def fail_checkpoint(env) -> None:
        raise OSError("disk full")

    # Tanpa saran yang bisa dijalankan (payload_ids kosong), langkah proaktif
    # datang dari `state`; di sini kita cukup membuktikan bahwa checkpoint gagal
    # memblokir langkah destruktif yang dipaksakan lewat metadata.
    report = run_autopilot(
        target="https://example.com", run_probe=probe,
        destructive_allowed=True, payload_ids=["BL-001"],
        vuln_class="__none__", adaptive=False,
        max_cycles=1, max_steps_per_cycle=1,
        checkpoint=fail_checkpoint,
    )
    assert not calls, "langkah destruktif tidak boleh dikirim saat checkpoint gagal"
    assert report.stop_incomplete is True
