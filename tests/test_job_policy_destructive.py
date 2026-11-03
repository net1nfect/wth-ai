"""Regression tests — WU-008: fix dead-fallthrough `jobs/policy.py::PolicyEngine.evaluate()`
(AUTHZ-05, SECURITY_AUDIT.md).

Bug asli: baris terakhir `evaluate()` selalu `return True, ""` tanpa syarat --
parameter `safe_mode` (default True) dicek lalu diabaikan sepenuhnya (CWE-670).
Docstring modul menjanjikan "Rejects out-of-policy jobs before they enter the
queue" tapi tidak ada job type yang pernah benar-benar ditolak lewat jalur ini
selain unknown-type/missing-target/forbidden-pattern.

Scope fix ini SENGAJA sempit -- persis rekomendasi & contoh verifikasi di
SECURITY_AUDIT.md: `PolicyEngine.evaluate(type="executor", safe_mode=True)`
untuk task berisiko tinggi harus (False, ...) kecuali unrestricted. "executor"
dipilih karena itu satu-satunya job type yang menjalankan modul serang aktif
(handler_executor -> run_executor_job), BUKAN authorization-metadata baru --
opsi itu ditolak karena tumpang tindih dengan gate authorization yang sudah
benar di ReconEngine (WU-003) dan akan merusak >=6 test existing yang membuat
job "recon" tanpa metadata. Tidak ada caller (source maupun test) yang pernah
membuat job type="executor" di seluruh codebase (diverifikasi via grep) --
maka fix ini punya blast radius nol terhadap behavior existing.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.config import WTHConfig
from wth_ai.events import EventBus
from wth_ai.jobs.manager import JobManager
from wth_ai.jobs.policy import PolicyEngine


@contextlib.contextmanager
def temp_home():
    import wth_ai.config as cfg_mod
    from wth_ai.workspace import init_workspace
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        old_home = os.environ.get("WTH_HOME")
        old_cfg = cfg_mod.USER_CONFIG_PATH
        old_env = cfg_mod.USER_ENV_PATH
        os.environ["WTH_HOME"] = str(base)
        cfg_mod.USER_CONFIG_PATH = base / "config" / "user.yaml"
        cfg_mod.USER_ENV_PATH = base / ".env"
        init_workspace(base)
        try:
            yield base
        finally:
            cfg_mod.USER_CONFIG_PATH = old_cfg
            cfg_mod.USER_ENV_PATH = old_env
            if old_home is None:
                os.environ.pop("WTH_HOME", None)
            else:
                os.environ["WTH_HOME"] = old_home


def _manager(bus=None) -> JobManager:
    return JobManager(WTHConfig(), bus=bus or EventBus())


# ---------------------------------------------------------------------------
# Unit level -- PolicyEngine.evaluate() langsung
# ---------------------------------------------------------------------------

def test_executor_denied_by_default_safe_mode(monkeypatch):
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, reason = PolicyEngine(WTHConfig()).evaluate(
        type="executor", target="x.com", safe_mode=True
    )
    assert allowed is False, (
        "REGRESI AUTHZ-05: safe_mode=True untuk job type destructive "
        "('executor') masih fallthrough ke True -- bug asli belum diperbaiki"
    )
    assert "destructive" in reason


def test_executor_denied_when_safe_mode_default_omitted(monkeypatch):
    """safe_mode punya default True -- caller yang tidak eksplisit set pun harus tetap deny."""
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, reason = PolicyEngine(WTHConfig()).evaluate(type="executor", target="x.com")
    assert allowed is False
    assert "destructive" in reason


def test_executor_allowed_when_unrestricted(monkeypatch):
    """Positive/compatibility: capability unrestricted (dimensi 2, WU-003) tetap
    bisa mengizinkan job destructive -- fix ini tidak menghapus kapabilitas itu."""
    monkeypatch.setenv("WTH_UNRESTRICTED", "1")
    try:
        allowed, reason = PolicyEngine(WTHConfig()).evaluate(
            type="executor", target="x.com", safe_mode=True
        )
        assert allowed is True
        assert reason == ""
    finally:
        monkeypatch.setenv("WTH_UNRESTRICTED", "0")


def test_executor_allowed_with_explicit_safe_mode_false(monkeypatch):
    """Positive/compatibility: override eksplisit safe_mode=False tetap dihormati
    (sesuai kontrak parameter -- bukan dihapus, cuma tidak lagi diabaikan)."""
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, reason = PolicyEngine(WTHConfig()).evaluate(
        type="executor", target="x.com", safe_mode=False
    )
    assert allowed is True
    assert reason == ""


def test_recon_unaffected_by_destructive_gate(monkeypatch):
    """Compatibility kunci: "recon" BUKAN destructive type -- fix ini tidak
    menyentuh behavior recon sama sekali (beda konsern dari WU-003)."""
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, reason = PolicyEngine(WTHConfig()).evaluate(
        type="recon", target="x.com", safe_mode=True
    )
    assert allowed is True
    assert reason == ""


def test_custom_report_provider_test_unaffected_by_destructive_gate(monkeypatch):
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    for jtype in ("custom", "report", "provider_test"):
        allowed, reason = PolicyEngine(WTHConfig()).evaluate(type=jtype, safe_mode=True)
        assert allowed is True, f"job type {jtype!r} seharusnya tidak terpengaruh gate destructive"


def test_unknown_type_and_missing_target_checks_still_run_before_destructive_gate(monkeypatch):
    """Urutan pengecekan lama (unknown type, missing target, forbidden pattern)
    tidak berubah -- destructive gate hanya tambahan di akhir."""
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, reason = PolicyEngine(WTHConfig()).evaluate(type="nope", safe_mode=True)
    assert allowed is False and "unknown job type" in reason

    allowed, reason = PolicyEngine(WTHConfig()).evaluate(type="executor", target=None, safe_mode=True)
    assert allowed is False and "requires a target" in reason

    allowed, reason = PolicyEngine(WTHConfig()).evaluate(
        type="executor", target="evil-internal.local", forbidden=("internal",), safe_mode=True
    )
    assert allowed is False and "forbidden" in reason


# ---------------------------------------------------------------------------
# End-to-end -- lewat JobManager.create_job() (caller nyata satu-satunya)
# ---------------------------------------------------------------------------

def test_manager_create_executor_job_denied_end_to_end():
    """JobManager.create_job() tidak pernah mengoper safe_mode eksplisit
    (default True dipakai) -- membuktikan fix ini benar-benar menutup celah
    end-to-end, bukan cuma di level fungsi terisolasi."""
    with temp_home():
        mgr = _manager()
        job = mgr.create_job("executor", target="x.com")
        assert job.status == "failed"
        assert "policy" in job.error
        assert "destructive" in job.error
        assert mgr.list_jobs(status="queued") == []


def test_manager_create_executor_job_allowed_when_unrestricted(monkeypatch):
    """Positive/compatibility end-to-end: unrestricted tetap mengizinkan job
    executor lewat JobManager, tidak ada regresi kapabilitas."""
    with temp_home():
        monkeypatch.setenv("WTH_UNRESTRICTED", "1")
        try:
            mgr = _manager()
            job = mgr.create_job("executor", target="x.com")
            assert job.status != "failed"
        finally:
            monkeypatch.setenv("WTH_UNRESTRICTED", "0")


def test_manager_create_recon_job_unaffected_end_to_end():
    """Compatibility: alur recon lewat JobManager (dipakai luas di test_jobs.py)
    sama sekali tidak berubah oleh fix ini."""
    with temp_home():
        mgr = _manager()
        job = mgr.create_job("recon", target="x.com")
        assert job.status != "failed"
