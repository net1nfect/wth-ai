"""Pustaka payload destruktif + autopilot: cakupan lengkap, dampak berbatas.

Properti yang dijaga dan diuji di sini:
* katalog LENGKAP lintas kelas (bukan daftar CVE) dan setiap entri menyatakan apa yang
  dibuktikan + cara memulihkan keadaannya;
* payload `mass` TIDAK PERNAH dijalankan mesin, dan setiap payload massal punya
  padanan berbatas yang membuktikan kelas bug yang sama;
* payload destruktif hanya jalan bila engagement membuka opt-in;
* payload injection ikke merusak data (tidak ada DROP/rm -rf di katalog);
* dokumentasi `.md` digenerate dari katalog sehingga tidak bisa menyimpang.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.hunt.autopilot import AutopilotReport, run_autopilot
from wth_ai.hunt.payloads import (
    BLAST_ORDER,
    PAYLOADS,
    filter_payloads,
    get_payload,
    render_probe,
    to_markdown,
)

REQUIRED_CLASSES = {
    "business_logic", "state_machine", "race", "mass_assignment", "idor_write",
    "injection_detect", "file_path", "cache_desync", "api_graphql", "auth_session",
    "cloud_storage", "host_header",
}


# --------------------------------------------------------------------------- #
# katalog
# --------------------------------------------------------------------------- #
def test_catalog_covers_every_intended_class_not_just_popular_cves():
    classes = {payload.vuln_class for payload in PAYLOADS}
    missing = REQUIRED_CLASSES - classes
    assert not missing, f"kelas belum tercakup: {sorted(missing)}"
    assert len(PAYLOADS) >= 40, f"katalog terlalu tipis: {len(PAYLOADS)}"


def test_every_payload_states_what_it_proves_and_how_to_recover():
    for payload in PAYLOADS:
        assert payload.id and payload.title, payload
        assert payload.proves, f"{payload.id} tidak menyatakan apa yang dibuktikan"
        assert payload.probe.get("url"), f"{payload.id} tanpa probe url"
        assert payload.blast_radius in BLAST_ORDER, payload.id
        if payload.requires_destructive:
            assert payload.cleanup, f"{payload.id} destruktif tanpa langkah pemulihan"


def test_ids_are_unique():
    ids = [payload.id for payload in PAYLOADS]
    assert len(ids) == len(set(ids))


def test_mass_payloads_are_never_auto_runnable_and_have_a_bounded_alternative():
    """Properti keselamatan inti: yang berdampak massal tidak pernah dijalankan mesin."""
    mass = [payload for payload in PAYLOADS if payload.blast_radius == "mass"]
    assert mass, "harus ada entri massal sebagai dokumentasi"
    for payload in mass:
        assert payload.auto_runnable is False, f"{payload.id} massal tapi bisa dijalankan otomatis"
        assert payload.bounded_alternative, f"{payload.id} massal tanpa padanan berbatas"


def test_injection_payloads_contain_no_destructive_commands():
    """Katalog tidak memuat payload yang merusak data -- itu janji dokumennya."""
    forbidden = ("drop table", "rm -rf", "shutdown", "truncate table", "delete from", "format c:")
    for payload in PAYLOADS:
        blob = " ".join(str(v).lower() for v in payload.probe.values())
        if payload.vuln_class != "injection_detect":
            continue
        for token in forbidden:
            assert token not in blob, f"{payload.id} memuat perintah perusak: {token}"


def test_filters_never_return_destructive_or_mass_by_default():
    default = filter_payloads()
    assert default, "harus ada payload default"
    assert all(not p.requires_destructive for p in default)
    assert all(p.blast_radius != "mass" for p in default)
    assert all(p.auto_runnable for p in default)


def test_opt_in_unlocks_single_object_but_never_mass():
    with_destructive = filter_payloads(None, include_destructive=True)
    assert len(with_destructive) > len(filter_payloads())
    assert all(p.blast_radius != "mass" for p in with_destructive), "massal harus tetap keluar"
    assert any(p.requires_destructive for p in with_destructive)


def test_class_filter_and_single_object_limit():
    items = filter_payloads("idor_write", include_destructive=True)
    assert items and all(p.vuln_class == "idor_write" for p in items)
    assert all(BLAST_ORDER[p.blast_radius] <= BLAST_ORDER["single-object"] for p in items)


def test_render_probe_fills_every_placeholder():
    payload = get_payload("IW-001")
    assert payload is not None
    rendered = render_probe(payload, base="http://t", marker="M1", other_id="2")
    blob = " ".join(str(v) for v in rendered.values())
    assert "{base}" not in blob and "{marker}" not in blob and "{other_id}" not in blob
    assert "http://t/api/items/2" in blob


def test_markdown_documents_every_payload_and_the_bounds():
    md = to_markdown()
    for payload in PAYLOADS:
        assert payload.id in md, f"{payload.id} tidak ada di dokumentasi"
    assert "blast_radius" in md and "auto_runnable=False" in md
    assert f"**{len(PAYLOADS)} payload**" in md
    assert "bukan sensor" in md.lower() or "Bukan sensor" in md


# --------------------------------------------------------------------------- #
# autopilot
# --------------------------------------------------------------------------- #
class _State:
    def __init__(self) -> None:
        self.metadata: dict = {}
        scope = type("ScopeStub", (), {})
        scope.destructive_allowed = False
        scope.target = "http://t"
        self.scope = scope


class _Result:
    def __init__(self, status: int = 200, tx: str = "TXauto") -> None:
        self.status_code = status
        self.transcript_id = tx
        self.stdout = f"body status {status}"


def _runner(record: list[dict], status: int = 200):
    def _run(probe: dict):
        record.append(probe)
        return _Result(status, f"TX{len(record)}")
    return _run


def test_autopilot_runs_bounded_steps_and_records_evidence():
    state, calls = _State(), []
    report = run_autopilot(target="http://t", run_probe=_runner(calls), state=state,
                           max_cycles=1, max_steps_per_cycle=4)
    assert report.ran > 0, report.to_dict()
    assert len(calls) == report.ran
    pairs = state.metadata.get("request_pairs", [])
    assert pairs, "bukti request/response harus tercatat"
    assert all(pair["response_status"] == 200 for pair in pairs)


def test_autopilot_never_runs_a_mass_payload_even_with_opt_in():
    state, calls = _State(), []
    state.scope.destructive_allowed = True
    report = run_autopilot(target="http://t", run_probe=_runner(calls), state=state,
                           max_cycles=1, max_steps_per_cycle=20,
                           payload_ids=["IW-004", "MA-005", "IN-009", "ST-005", "CL-004"])
    mass_ids = {p.id for p in PAYLOADS if p.blast_radius == "mass"}
    ran_payload_ids = {step.probe.get("payload_id") for step in report.steps if step.ran}
    assert not (ran_payload_ids & mass_ids), f"payload massal dijalankan: {ran_payload_ids & mass_ids}"


def test_autopilot_blocks_destructive_payload_without_opt_in():
    state, calls = _State(), []
    report = run_autopilot(target="http://t", run_probe=_runner(calls), state=state,
                           max_cycles=1, max_steps_per_cycle=3, payload_ids=["IW-001"])
    blocked = [step for step in report.steps if step.blocked_reason]
    assert blocked, "payload destruktif harus tercatat sebagai diblokir"
    assert "opt-in" in blocked[0].blocked_reason
    assert all("items/2" not in str(call.get("url", "")) for call in calls), "tidak boleh dijalankan"


def test_autopilot_runs_destructive_payload_when_the_engagement_opts_in():
    state, calls = _State(), []
    state.scope.destructive_allowed = True
    report = run_autopilot(target="http://t", run_probe=_runner(calls), state=state,
                           max_cycles=1, max_steps_per_cycle=1, payload_ids=["IW-001"])
    assert report.ran == 1
    assert report.destructive_used is True
    assert "PERINGATAN" in __import__("wth_ai.hunt.autopilot", fromlist=["format_report"]).format_report(report)


def test_dry_run_plans_without_touching_the_target():
    state, calls = _State(), []
    report = run_autopilot(target="http://t", run_probe=_runner(calls), state=state,
                           max_cycles=1, max_steps_per_cycle=5, dry_run=True)
    assert report.ran == 0 and calls == []
    assert report.steps, "rencana tetap harus disusun"
    assert any("dry-run" in step.note for step in report.steps)


def test_autopilot_learns_from_each_evidence_backed_step():
    state, calls, learned = _State(), [], []
    run_autopilot(target="http://t", run_probe=_runner(calls), state=state,
                  max_cycles=1, max_steps_per_cycle=3, learn=learned.append)
    assert learned, "callback belajar harus dipanggil"
    assert all(step.evidence_ref for step in learned)


def test_autopilot_reproposes_from_new_signals_next_cycle():
    """Adaptif: sinyal baru dari cycle 1 harus memunculkan usulan baru di cycle 2."""
    state, calls = _State(), []

    def _run(probe: dict):
        calls.append(probe)
        # semua menjawab 403 supaya aturan sinyal "endpoint menolak" aktif
        return _Result(403, f"TX{len(calls)}")

    report = run_autopilot(target="http://t", run_probe=_run, state=state,
                           max_cycles=2, max_steps_per_cycle=3)
    titles = " ".join(step.title for step in report.steps).lower()
    assert "menolak" in titles, f"usulan dari sinyal 403 tidak muncul: {titles[:200]}"
    assert report.cycles == 2


def test_report_format_is_honest_about_nothing_happening():
    report = AutopilotReport(target="http://t", stopped_reason="tidak ada kandidat")
    from wth_ai.hunt.autopilot import format_report

    text = format_report(report)
    assert "tidak ada langkah" in text or "tidak ada kandidat" in text
