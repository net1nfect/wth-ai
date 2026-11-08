"""P1-6: gate otorisasi job executor dibaca dari scope card (P0-1).

Temuan audit segar 2026-11-06: `run_executor_job` di
`wth_ai/jobs/orchestrator_bridge.py` membaca `meta.get("authorized", True)` —
default `True` berarti job TANPA penanda `authorized` tetap dieksekusi
(fail-open). Revisi: fail-closed default `False` membuat SEMUA job executor
ditolak, karena tidak ada pemanggil yang menanami `authorized` di metadata job
executor (hanya `recon` yang di-stamp). Kedua-duanya salah: sesuai invariant
P0-1, otorisasi harus dibaca dari artifact scope card (`scope_card.json`),
bukan metadata job — sama seperti `handler_recon` dan `handler_autopilot`.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")


class _FakeJob:
    def __init__(self, *, target: str, metadata: dict):
        self.target = target
        self.metadata = metadata
        self.job_id = "job-1"
        self.profile = None


class _FakeCtx:
    def should_stop(self):
        return False

    def progress(self, *args):
        pass

    def log(self, *args):
        pass


def _write_scope_card(monkeypatch, tmp_path, *, authorized=True):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    card = {
        "target": "example.com",
        "domains": ["example.com"],
        "cidr": [],
        "authorized": authorized,
        "mode": "standard",
        "destructive": False,
    }
    path = tmp_path / "home" / "scope_card.json"
    path.write_text(json.dumps(card), encoding="utf-8")
    return path


def _run(monkeypatch, tmp_path, job):
    from wth_ai.jobs.orchestrator_bridge import run_executor_job

    return run_executor_job(job, _FakeCtx())


def test_executor_job_without_scope_card_is_refused(monkeypatch, tmp_path):
    """Tanpa scope card, job HARUS ditolak (fail-closed, P0-1)."""
    job = _FakeJob(target="example.com", metadata={})
    res = _run(monkeypatch, tmp_path, job)
    assert res["status"] == "failed", res
    assert "scope" in (res.get("error", "") or "").lower(), res


def test_executor_job_with_non_authorized_scope_card_is_refused(monkeypatch, tmp_path):
    _write_scope_card(monkeypatch, tmp_path, authorized=False)
    job = _FakeJob(target="example.com", metadata={})
    res = _run(monkeypatch, tmp_path, job)
    assert res["status"] == "failed", res
    assert "scope" in (res.get("error", "") or "").lower(), res


def test_executor_job_with_authorized_scope_card_proceeds(monkeypatch, tmp_path):
    """Dengan scope card valid, job lanjut (bukan gagal karena 'not authorized').

    Hasil akhir boleh apa pun (scope/permission/task kosong dst) — yang penting
    bukan ditolak di gate otorisasi."""
    _write_scope_card(monkeypatch, tmp_path, authorized=True)
    job = _FakeJob(target="example.com", metadata={})
    res = _run(monkeypatch, tmp_path, job)
    assert res.get("error", "") != "not authorized", res
    assert res["status"] != "failed", res


def test_executor_job_metadata_authorized_is_ignored(monkeypatch, tmp_path):
    """Metadata `authorized` TIDAK boleh jadi sumber otorisasi (P0-1):
    tanpa scope card valid, tetap ditolak meski metadata bilang authorized."""
    _write_scope_card(monkeypatch, tmp_path, authorized=False)
    job = _FakeJob(target="example.com", metadata={"authorized": True})
    res = _run(monkeypatch, tmp_path, job)
    assert res["status"] == "failed", res
    assert "scope" in (res.get("error", "") or "").lower(), res
