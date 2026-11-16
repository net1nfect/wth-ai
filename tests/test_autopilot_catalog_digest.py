"""Checkpoint vs katalog: digest definisi payload harus menjaga resume.

Bug yang dicegah:

* `verify_probe`/`verify_fields` TIDAK disimpan di checkpoint — resume
  membangunnya lagi dari katalog. Bila definisi payload berubah antar-proses,
  bukti `COMPLETED` lama bisa menunjuk objek/field yang berbeda.
* Tanpa deteksi, langkah yang sudah "selesai" dilewati diam-diam, sehingga
  cakupan katalog baru tidak pernah diuji.
* Deteksi yang terlalu kasar (mis. menolak resume lalu mengulang semua)
  berbahaya untuk langkah destruktif: efeknya bisa terjadi dua kali.

Kontrak yang diuji:

* setiap langkah payload menyimpan digest definisinya di checkpoint;
* digest yang berbeda menolak bukti `COMPLETED` lama dan dilaporkan eksplisit;
* langkah destruktif yang definisinya berubah TIDAK dijalankan ulang otomatis;
* langkah non-destruktif dijalankan ulang dengan definisi saat ini;
* checkpoint lama tanpa digest tetap kompatibel;
* setelah record dengan digest saat ini ada, resume berikutnya tidak menolak
  terus-menerus.
"""

from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.hunt import payloads
from wth_ai.hunt.autopilot import (
    AutopilotStep,
    _stale_definition_ids,
    run_autopilot,
)
from wth_ai.hunt.payloads import (
    get_payload,
    payload_definition_digest,
)
from wth_ai.jobs.checkpoint import StepOutcome

TARGET = "https://example.com"

#: Body operasi BL-001. Dipakai untuk membedakan langkah ini dari payload lain
#: yang kebetulan menembak method+URL sama (BL-002 juga POST /api/cart).
BL001_OPERATION_BODY = '{"item_id":"1","qty":-1}'


class _Resp:
    def __init__(self, body: str = "ok", status: int = 200):
        self.status_code = status
        self.stdout = body
        self.transcript_id = f"TX-{status}"
        self.response_headers = {}


def _cart_probe(calls: list[dict]):
    """Stub untuk BL-001: anchor GET kembali ke keadaan awal."""

    def run(probe):
        calls.append(dict(probe))
        if str(probe.get("method", "GET")).upper() == "GET":
            return _Resp('{"qty": 1}')
        return _Resp('{"ok": true}', status=201)

    return run


def _plain_probe(calls: list[dict]):
    def run(probe):
        calls.append(dict(probe))
        return _Resp("ok")

    return run


def _run(probe, **kw):
    base = dict(
        target=TARGET,
        max_cycles=1,
        max_steps_per_cycle=1,
        payload_ids=["BL-001"],
        adaptive=False,
        destructive_allowed=True,
    )
    base.update(kw)
    return run_autopilot(run_probe=probe, **base)


def _operations(calls: list[dict], body: str) -> list[dict]:
    return [probe for probe in calls if probe.get("body") == body]


def test_digest_is_stable_and_sensitive_to_anchor_changes():
    payload = get_payload("BL-001")
    assert payload is not None
    first = payload_definition_digest(payload)
    assert first == payload_definition_digest(payload)
    changed = replace(payload, verify_fields=("qty", "item_id"))
    assert payload_definition_digest(changed) != first
    # Perubahan cleanup juga harus terdeteksi: cleanup adalah bagian kontrak
    # pemulihan, bukan metadata tampilan.
    changed_cleanup = replace(payload, cleanup_probe={})
    assert payload_definition_digest(changed_cleanup) != first


def test_checkpoint_persists_definition_digest():
    report = _run(_cart_probe([]))
    digest = payload_definition_digest(get_payload("BL-001"))
    seed = next(
        step for step in report.steps
        if step.probe.get("payload_id") == "BL-001"
    )
    assert seed.outcome == StepOutcome.COMPLETED.value
    assert seed.definition_digest == digest
    stored = report.checkpoint.engine_state["steps"]
    assert any(
        item.get("definition_digest") == digest for item in stored
    ), "digest definisi harus ikut checkpoint, bukan hanya state di memori"


def test_same_catalog_resume_still_skips_completed_operation():
    seed = _run(_cart_probe([]))
    calls: list[dict] = []
    report = _run(_cart_probe(calls), max_cycles=2, resume=seed.checkpoint)
    assert report.resume_rejected_reason == ""
    assert not _operations(calls, BL001_OPERATION_BODY), (
        "katalog sama: operasi COMPLETED tidak boleh diulang"
    )


def test_changed_definition_rejects_completed_evidence_and_blocks_destructive(
    monkeypatch,
):
    seed = _run(_cart_probe([]))
    monkeypatch.setattr(
        payloads,
        "PAYLOADS",
        tuple(
            replace(payload, verify_fields=("qty", "item_id"))
            if payload.id == "BL-001" else payload
            for payload in payloads.PAYLOADS
        ),
    )

    calls: list[dict] = []
    report = _run(_cart_probe(calls), max_cycles=2, resume=seed.checkpoint)

    assert "definisi payload berubah" in report.resume_rejected_reason
    assert "BL-001" in report.resume_rejected_reason
    # Anchor berubah => bukti lama tidak berlaku. Langkah destruktif TIDAK
    # boleh diulang otomatis; operator harus memverifikasi keadaan dulu.
    assert not _operations(calls, BL001_OPERATION_BODY), (
        "langkah destruktif dengan definisi berubah tidak boleh dijalankan ulang"
    )
    blocked = [
        step for step in report.steps
        if step.probe.get("payload_id") == "BL-001"
        and step.outcome == StepOutcome.UNKNOWN.value
    ]
    assert blocked, "bukti COMPLETED lama harus diturunkan ke UNKNOWN"
    assert any(step.blocked_reason for step in blocked)


def test_changed_non_destructive_definition_is_rerun(monkeypatch):
    seed = _run(_plain_probe([]), payload_ids=["IN-001"])
    monkeypatch.setattr(
        payloads,
        "PAYLOADS",
        tuple(
            replace(payload, title=payload.title + " (revisi)")
            if payload.id == "IN-001" else payload
            for payload in payloads.PAYLOADS
        ),
    )

    calls: list[dict] = []
    report = _run(
        _plain_probe(calls), payload_ids=["IN-001"], max_cycles=2,
        resume=seed.checkpoint,
    )

    assert "definisi payload berubah" in report.resume_rejected_reason
    assert any(
        "1 AND 1=1" in str(probe.get("url", "")) for probe in calls
    ), "langkah non-destruktif harus dijalankan ulang dengan definisi baru"


def test_legacy_checkpoint_without_digest_is_not_rejected():
    seed = _run(_cart_probe([]))
    for item in seed.checkpoint.engine_state["steps"]:
        item.pop("definition_digest", None)

    calls: list[dict] = []
    report = _run(_cart_probe(calls), max_cycles=2, resume=seed.checkpoint)

    assert report.resume_rejected_reason == ""
    assert not _operations(calls, BL001_OPERATION_BODY)


def test_current_definition_record_stops_repeated_rejection():
    payload = get_payload("BL-001")
    current = payload_definition_digest(payload)
    old = AutopilotStep(
        kind="payload", title="BL-001 lama",
        probe={"payload_id": "BL-001"}, definition_digest="digest-lama",
    )
    new = AutopilotStep(
        kind="payload", title="BL-001 baru",
        probe={"payload_id": "BL-001"}, definition_digest=current,
    )
    assert _stale_definition_ids([old]) == {"BL-001": current}
    assert _stale_definition_ids([old, new]) == {}
