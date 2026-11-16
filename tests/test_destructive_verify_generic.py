"""Verifier generik untuk payload tanpa indikator statis (mis. `GEN-*`).

Yang diuji adalah BATAS KLAIM, bukan sekadar jalur kode:

1. status/header yang sama sebelum & sesudah TIDAK PERNAH cukup untuk
   `verified` — itu hanya membuktikan shape respons tidak berubah;
2. marker yang disuntikkan payload menjadi bukti HANYA bila transisinya
   terlihat (ada sebelum, hilang sesudah);
3. marker yang tidak pernah terlihat => `unavailable`, bukan sukses dan bukan
   "target rusak";
4. payload GEN-* yang destruktif tetap mendapat anchor generik, tapi hasilnya
   `UNKNOWN` bila tidak ada bukti;
5. anchor generik tidak membawa body/header operasi (tidak mengulang
   operasi saat membaca keadaan).

Semua probe adalah fungsi lokal; tidak ada jaringan.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest
from types import SimpleNamespace

from wth_ai.hunt.autopilot import generic_verify_probe, run_autopilot
from wth_ai.hunt.destructive_verify import (
    DEFAULT_EFFECT_MARKER,
    EffectAnchor,
    indicator_candidates,
    posture_diff,
    verify_cleanup,
    verify_effect,
)
from wth_ai.jobs.checkpoint import StepOutcome

TARGET = "https://example.com/api/items/42"
MARKER = DEFAULT_EFFECT_MARKER


class _Resp:
    """Bentuk hasil probe kanonik: `.stdout` berisi body (dict baru per objek).

    Stub WAJIB membuat objek baru per pembacaan. Satu objek yang dipakai ulang
    dengan body yang diubah akan mengubah bacaan sebelumnya juga — pada
    dasarnya meniru server yang tidak pernah berubah.
    """

    def __init__(
        self, status: int = 200, body: str = "ok", headers: dict | None = None
    ) -> None:
        self.status_code = status
        self.stdout = body
        self.transcript_id = f"TX-{status}"
        self.response_headers = dict(headers or {})


def _after_reader(after_body: str, *, status: int = 200, headers: dict | None = None):
    """`run_probe` yang mengembalikan KEADAAN SESUDAH operasi.

    Kontrak `verify_effect`/`verify_cleanup`: anchor dibaca **sekali**, tepat
    sesudah operasi. Keadaan sebelum tidak dibaca ulang — ia diteruskan
    sebagai `before_body`/`before_posture` dari pembacaan yang dilakukan
    SEBELUM operasi dijalankan. Stub yang mengembalikan body "sebelum" pada
    pembacaan ini bukan menguji transisi; ia menguji kasus "efek masih ada",
    dan memang harus ditolak.
    """
    def run(probe):
        return _Resp(status=status, body=after_body, headers=headers)

    return run


ANCHOR = EffectAnchor(probe={"method": "GET", "url": TARGET})


# ---------------------------------------------------------------------------
# imperative: verifier generic
# ---------------------------------------------------------------------------


def test_generic_indicator_is_the_injected_marker():
    assert indicator_candidates("GEN-business-logic-some-title") == (MARKER,)
    # payload katalog tetap memakai indikator statisnya
    assert "-1" in indicator_candidates("BL-001")


def test_same_status_and_headers_alone_never_verify():
    """Shape respons sama BUKAN bukti pemulihan efek state."""
    result = verify_effect(
        payload_id="GEN-business-logic-x",
        anchor=ANCHOR,
        run_probe=_after_reader('{"id": 42}'),
        before_body='{"id": 42}',
        before_posture={"status": "200", "headers": {"x-frame-options": "DENY"}},
    )
    assert result.verified is False
    assert result.unavailable is True
    assert result.marker_transition is False
    assert "marker uji tidak pernah terlihat" in result.reason


def test_marker_disappearing_after_operation_is_verified():
    result = verify_effect(
        payload_id="GEN-business-logic-x",
        anchor=ANCHOR,
        run_probe=_after_reader('{"id": 42}'),
        before_body=f'{{"note": "{MARKER}", "id": 42}}',
    )
    assert result.verified is True
    assert result.marker_transition is True
    assert MARKER in result.reason


def test_marker_still_present_is_not_verified():
    result = verify_effect(
        payload_id="GEN-business-logic-x",
        anchor=ANCHOR,
        run_probe=_after_reader(f'{{"note": "{MARKER}"}}'),
        before_body=f'{{"note": "{MARKER}"}}',
    )
    assert result.verified is False
    assert result.unavailable is False
    assert "masih terlihat" in result.reason


def test_marker_never_seen_is_unavailable_not_failure():
    result = verify_effect(
        payload_id="GEN-business-logic-x",
        anchor=ANCHOR,
        run_probe=_after_reader("b"),
        before_body="a",
    )
    assert result.verified is False
    assert result.unavailable is True


def test_posture_diff_reports_status_and_header_changes():
    diff = posture_diff(
        {"status": "200", "headers": {"x-a": "1"}},
        {"status": "403", "headers": {"x-a": "2"}},
    )
    assert diff["status"] == {"before": "200", "after": "403"}
    assert diff["headers"]["x-a"] == {"before": "1", "after": "2"}


def test_posture_diff_ignores_volatile_headers():
    diff = posture_diff(
        {"status": "200", "headers": {"date": "Mon", "etag": "a"}},
        {"status": "200", "headers": {"date": "Tue", "etag": "b"}},
    )
    assert diff == {}


def test_posture_difference_is_mentioned_in_rejection_reason():
    result = verify_effect(
        payload_id="GEN-business-logic-x",
        anchor=ANCHOR,
        run_probe=_after_reader("plain", status=500, headers={"x-err": "boom"}),
        before_body="plain",
        before_posture={"status": "200", "headers": {}},
    )
    assert result.verified is False
    assert "status 200->500" in result.reason
    assert "x-err" in result.reason


def test_generic_cleanup_needs_prior_marker_evidence():
    result = verify_cleanup(
        payload_id="GEN-business-logic-x",
        anchor=ANCHOR,
        run_probe=_after_reader("clean"),
        before_had_marker=False,
    )
    assert result.verified is False
    assert result.unavailable is True


def test_generic_cleanup_verified_when_marker_was_present_before():
    result = verify_cleanup(
        payload_id="GEN-business-logic-x",
        anchor=ANCHOR,
        run_probe=_after_reader("clean"),
        before_had_marker=True,
    )
    assert result.verified is True


def test_static_indicator_payload_cleanup_unchanged():
    """Payload katalog tidak boleh berubah perilakunya karena jalur generic."""
    result = verify_cleanup(
        payload_id="BL-001",
        anchor=ANCHOR,
        run_probe=_after_reader('{"qty": 2}'),
    )
    assert result.verified is True


def test_missing_expected_field_is_reported_not_verified():
    anchor = EffectAnchor(probe={"method": "GET", "url": TARGET}, expected=("qty",))
    result = verify_effect(
        payload_id="GEN-business-logic-x",
        anchor=anchor,
        run_probe=_after_reader("{}"),
        before_body=f'{{"{MARKER}": 1, "qty": 1}}',
    )
    assert result.verified is False
    assert "qty" in result.reason


# ---------------------------------------------------------------------------
# anchor generik
# ---------------------------------------------------------------------------


def test_generic_probe_uses_read_only_method():
    assert generic_verify_probe({"method": "POST", "url": TARGET}) == {
        "method": "GET", "url": TARGET,
    }
    assert generic_verify_probe({"method": "DELETE", "url": TARGET})["method"] == "GET"
    assert generic_verify_probe({"method": "HEAD", "url": TARGET})["method"] == "HEAD"


def test_generic_probe_does_not_carry_operation_body_or_headers():
    out = generic_verify_probe({
        "method": "POST",
        "url": TARGET,
        "body": '{"marker": "x"}',
        "headers": {"X-Custom": "1"},
        "cookies": {"sid": "1"},
        "params": {"q": "1"},
    })
    assert "body" not in out
    assert "headers" not in out
    assert "cookies" not in out
    # GET tanpa param = membaca resource; param tidak diperlukan
    assert "params" not in out


# ---------------------------------------------------------------------------
# end-to-end: payload GEN-* destruktif
# ---------------------------------------------------------------------------


def _gen_payload_dict() -> dict:
    return {
        "id": "GEN-business-logic-generic-anchor",
        "vuln_class": "business_logic",
        "title": "Payload generik tanpa anchor (uji verifier)",
        "proves": "Verifier generik tidak boleh mengklaim sukses tanpa marker",
        "probe": {
            "method": "POST",
            "url": "https://example.com/api/notes",
            "body": '{"title": "wth-auto-9z8y7"}',
        },
        "blast_radius": "single-object",
        "reversible": True,
        "cleanup": "Hapus catatan uji",
        "requires_destructive": True,
        "auto_runnable": True,
    }


def _run_gen(monkeypatch, *, anchor_body: str, after_body: str):
    from wth_ai.hunt import generator, payloads

    payload, err = generator.validate_generated(_gen_payload_dict())
    assert payload is not None, err
    monkeypatch.setattr(
        payloads, "generated_payloads", lambda: [payload]
    )
    monkeypatch.setattr(
        generator, "load_generated", lambda path=None: [payload]
    )

    seen: list[dict] = []

    def run(probe):
        seen.append(dict(probe))
        if probe.get("method") == "POST":
            return _Resp(status=201, body="created")
        if probe.get("method") == "GET":
            return _Resp(body=after_body)
        return _Resp(status=404, body="")

    report = run_autopilot(
        target="https://example.com",
        run_probe=run,
        max_cycles=1,
        max_steps_per_cycle=1,
        destructive_allowed=True,
        payload_ids=[payload.id],
        adaptive=False,
        learn=lambda step: None,
    )
    return report, seen


def test_gen_payload_gets_generic_anchor_but_stays_unknown(monkeypatch):
    report, seen = _run_gen(
        monkeypatch,
        anchor_body="catatan ada",
        after_body="catatan ada",
    )
    step = report.steps[-1]
    assert step.title.startswith("GEN-business-logic-generic-anchor")
    anchor_calls = [p for p in seen if p.get("method") == "GET"]
    # Hanya SATU request mutasi: anchor generik dibaca read-only.
    assert len([p for p in seen if p.get("method") == "POST"]) == 1
    assert anchor_calls
    assert all(p.get("mutating") is False for p in anchor_calls)


def test_gen_payload_marked_unknown_when_no_transition(monkeypatch):
    report, _ = _run_gen(monkeypatch, anchor_body="catatan ada", after_body="catatan ada")
    step = report.steps[-1]
    assert step.outcome == StepOutcome.UNKNOWN.value
    assert not step.verification_ref
    assert "verifikasi sebelum mengulang" in step.note
