"""Kontrak verifier-jangkar untuk langkah destruktif (rekomendasi 1).

Yang diuji adalah KEPUTUSAN, bukan sekadar "tidak crash":

1. anchor read-only dibaca SEBELUM operasi (urutan request), sehingga
   pembanding tidak tercemar oleh operasi itu sendiri;
2. efek yang masih terlihat -> `UNKNOWN`, bukan `COMPLETED`;
3. `COMPLETED` destruktif hanya sah bila ada `verification_ref`;
4. payload tanpa anchor -> `unavailable`, bukan klaim sukses;
5. cleanup dijalankan hanya lewat `cleanup_probe` deklaratif dan hasilnya
   dilaporkan apa adanya (gagal = gagal).

Tidak ada jaringan: semua probe adalah fungsi lokal.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.hunt.autopilot import run_autopilot
from wth_ai.hunt.destructive_verify import (
    EffectAnchor,
    payload_effect_indicators,
    verify_cleanup,
    verify_effect,
)
from wth_ai.hunt.payloads import get_payload
from wth_ai.jobs.checkpoint import StepOutcome, make_step_key

TARGET = "https://example.com"
CART = "https://example.com/api/cart"


class _Resp:
    def __init__(self, status: int = 200, body: str = "ok") -> None:
        self.status_code = status
        self.stdout = body
        self.transcript_id = f"TX-{status}"
        self.response_headers = {}


def _run(**kw):
    base = dict(target=TARGET, max_cycles=1, max_steps_per_cycle=1, adaptive=False)
    base.update(kw)
    return run_autopilot(**base)


# ---------------------------------------------------------------------------
# unit: verify_effect
# ---------------------------------------------------------------------------


def test_effect_present_after_operation_is_not_verified():
    """Nilai khas payload yang masih ada = efek masih hidup."""
    anchor = EffectAnchor(probe={"method": "GET", "url": CART})
    after = '{"item_id":"1","qty":-1}'
    v = verify_effect(
        payload_id="BL-001",
        anchor=anchor,
        run_probe=lambda probe: _Resp(body=after),
        before_body='{"item_id":"1"}',
    )
    assert v.verified is False
    assert v.unavailable is False
    assert "-1" in v.reason


def test_anchor_restored_to_before_state_is_verified():
    anchor = EffectAnchor(probe={"method": "GET", "url": CART})
    v = verify_effect(
        payload_id="BL-001",
        anchor=anchor,
        run_probe=lambda probe: _Resp(body='{"item_id":"1"}'),
        before_body='{"item_id":"1"}',
    )
    assert v.verified is True and v.unavailable is False


def test_unreadable_anchor_is_unavailable_not_clean():
    """Kegagalan membaca tidak boleh disalahartikan sebagai "target bersih"."""
    anchor = EffectAnchor(probe={"method": "GET", "url": CART})
    v = verify_effect(
        payload_id="BL-001",
        anchor=anchor,
        run_probe=lambda probe: _Resp(status=0, body=""),
        before_body='{"item_id":"1"}',
    )
    assert v.verified is False
    assert v.unavailable is True


def test_no_anchor_is_unavailable():
    v = verify_effect(
        payload_id="BL-001", anchor=None,
        run_probe=lambda probe: _Resp(), before_body="",
    )
    assert v.verified is False and v.unavailable is True
    assert "anchor" in v.reason


def test_payload_without_registered_indicator_needs_manual_verification():
    """Payload yang tidak punya indikator terdaftar tidak boleh "verified"."""
    assert payload_effect_indicators("ST-001") == ()
    anchor = EffectAnchor(probe={"method": "GET", "url": CART})
    v = verify_effect(
        payload_id="ST-001", anchor=anchor,
        run_probe=lambda probe: _Resp(body='{"status":"cancelled"}'),
        before_body='{"status":"shipped"}',
    )
    assert v.verified is False and v.unavailable is True


# ---------------------------------------------------------------------------
# unit: verify_cleanup
# ---------------------------------------------------------------------------


def test_cleanup_verification_reports_leftover_marker():
    anchor = EffectAnchor(probe={"method": "GET", "url": "{base}/api/cart"})
    v = verify_cleanup(
        payload_id="BL-001", anchor=anchor,
        run_probe=lambda probe: _Resp(body='{"name":"wth-auto-9z8y7"}'),
    )
    assert v.verified is False
    assert "cleanup" in v.reason


def test_cleanup_verification_success():
    anchor = EffectAnchor(probe={"method": "GET", "url": "{base}/api/cart"})
    v = verify_cleanup(
        payload_id="BL-001", anchor=anchor,
        run_probe=lambda probe: _Resp(body='{"items":[]}'),
    )
    assert v.verified is True


# ---------------------------------------------------------------------------
# integrasi: urutan anchor -> operasi -> verifikasi -> cleanup
# ---------------------------------------------------------------------------


def test_anchor_is_read_before_the_destructive_operation():
    """Urutan request wajib: anchor dulu, baru operasi. Kalau dibalik,
    pembanding "sebelum" sudah tercemar dan verifikasi tidak bermakna."""
    log: list[tuple[str, str]] = []

    def probe(p: dict) -> _Resp:
        method = p.get("method", "GET")
        log.append((method, str(p.get("payload_id") or "")))
        if method == "GET":
            # Keadaan "bersih" sebelum operasi, tetap bersih sesudahnya.
            return _Resp(body='{"item_id":"1"}')
        return _Resp(body="ok")

    _run(run_probe=probe, destructive_allowed=True, payload_ids=["BL-001"],
         vuln_class="__none__")

    ops = [entry for entry in log if entry[0] == "POST"]
    assert ops and ops[0][1] == "BL-001"
    first_op_index = log.index(ops[0])
    anchors_before = [e for e in log[:first_op_index] if e[0] == "GET"]
    assert anchors_before, "anchor harus dibaca SEBELUM operasi destruktif"


def test_effect_still_present_keeps_step_unknown_and_blocks_rerun():
    """Operasi dijalankan, efek masih terlihat -> UNKNOWN, dan resume tidak
    mengulanginya tanpa verifikasi manual."""
    def probe(p: dict) -> _Resp:
        # `qty` wajib ada agar anchor bisa dibandingkan; nilai -1 adalah
        # indikator efek yang masih hidup.
        if p.get("mutating") is False:
            return _Resp(body='{"item_id":"1","qty":-1}')
        return _Resp(body="ok")

    report = _run(run_probe=probe, destructive_allowed=True,
                 payload_ids=["BL-001"], vuln_class="__none__")
    step = report.steps[0]
    assert step.outcome == StepOutcome.UNKNOWN.value
    assert step.verification_ref == ""
    # Alasan harus menyebut EFEK yang masih terlihat, bukan klaim generik.
    assert "masih terlihat" in step.note.lower()
    assert "-1" in step.note

    env = report.checkpoint
    assert env is not None
    rec = env.step(make_step_key("POST", CART))
    assert rec is not None and rec.outcome == StepOutcome.UNKNOWN
    assert rec.verified is False


def test_effect_cleared_marks_completed_with_evidence():
    def probe(p: dict) -> _Resp:
        # Field `qty` ada dan bersih (-1 hilang) = keadaan kembali ke jangkar.
        if p.get("mutating") is False:
            return _Resp(body='{"item_id":"1","qty":1}')
        return _Resp(body="ok")

    report = _run(run_probe=probe, destructive_allowed=True,
                  payload_ids=["BL-001"], vuln_class="__none__")
    step = report.steps[0]
    assert step.outcome == StepOutcome.COMPLETED.value
    assert step.verification_ref, "COMPLETED destruktif wajib punya bukti verifikasi"


def test_destructive_completed_without_verification_ref_is_downgraded():
    """Klasifikasi checkpoint tidak boleh mempercayai klaim engine: tanpa
    `verification_ref`, COMPLETED destruktif menjadi UNKNOWN."""
    from wth_ai.hunt.autopilot import _checkpoint_from_steps, AutopilotStep

    step = AutopilotStep(
        kind="payload", title="x",
        probe={"method": "POST", "url": CART, "payload_id": "BL-001",
               "destructive": True},
    )
    step.outcome = StepOutcome.COMPLETED.value
    step.verification_ref = ""
    env = _checkpoint_from_steps(target=TARGET, cursor=0, steps=[step])
    rec = env.step(make_step_key("POST", CART))
    assert rec is not None and rec.outcome == StepOutcome.UNKNOWN


def test_cleanup_request_is_sent_and_reported_honestly():
    """Cleanup hanya dikirim bila `cleanup_probe` deklaratif ada; hasilnya
    dilaporkan apa adanya, bukan diklaim berhasil."""
    sent: list[dict] = []

    def probe(p: dict) -> _Resp:
        sent.append(dict(p))
        if p.get("mutating") is False:
            return _Resp(body='{"item_id":"1","qty":1}')
        return _Resp(body="ok")

    report = _run(run_probe=probe, destructive_allowed=True,
                  payload_ids=["BL-001"], vuln_class="__none__")
    deletes = [p for p in sent if p.get("method") == "DELETE"]
    assert deletes, "cleanup deklaratif harus benar-benar dikirim"
    assert "cleanup" in report.steps[0].note.lower() or report.steps[0].cleanup_ref


def test_cleanup_failure_is_not_hidden():
    def probe(p: dict) -> _Resp:
        # DELETE diperiksa LEBIH DULU: cleanup probe juga bertanda
        # `mutating: False`, jadi urutan cabang menentukan respons yang
        # benar-benar diuji.
        if p.get("method") == "DELETE":
            return _Resp(status=500)
        if p.get("mutating") is False:
            return _Resp(body='{"item_id":"1","qty":-1}')
        return _Resp(body="ok")

    report = _run(run_probe=probe, destructive_allowed=True,
                  payload_ids=["BL-001"], vuln_class="__none__")
    step = report.steps[0]
    assert "cleanup" in step.note.lower()
    assert "gagal" in step.note.lower()


def test_payload_without_anchor_stays_unknown():
    """Payload katalog yang tidak punya anchor deklaratif tidak boleh
    menghasilkan COMPLETED walau responsnya 200."""
    # Payload hasil generasi kontrak tidak punya anchor deklaratif.
    payload_id = "GEN-business-logic-refund-without-ret"
    payload = get_payload(payload_id)
    assert payload is not None and not payload.verify_probe

    def probe(p: dict) -> _Resp:
        return _Resp(body="ok")

    report = _run(run_probe=probe, destructive_allowed=True,
                  payload_ids=[payload_id], vuln_class="__none__")
    step = report.steps[0]
    assert step.outcome == StepOutcome.UNKNOWN.value
    assert step.verification_ref == ""
