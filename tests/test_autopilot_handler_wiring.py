"""Regresi wiring handler_autopilot: dua kesalahan yang membuat probe gagal senyap.

Keduanya ketahuan dari UAT CLI nyata dengan target lokal:

1. `PermissionManager(config)` -- kelas itu menerima MODE, bukan config. Akibatnya
   `permission.mode` berisi objek config dan `mode_sufficient()` melempar
   "TypeError: unhashable type: 'WTHConfig'" pada SETIAP probe, sehingga langkah
   autopilot tercatat "dijalankan" dengan status kosong.
2. Bridge memakai ScopeManager sendiri; kalau scope itu tidak diisi target/in-scope,
   setiap request berhenti di gate scope (juga status 0).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.models import PermissionMode
from wth_ai.permission_manager import PermissionManager


class _Job:
    target = "127.0.0.1:9999"
    job_id = "J1"
    metadata = {"authorized": True, "steps": 1, "cycles": 1}


class _Ctx:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def progress(self, *args):
        pass

    def log(self, line):
        self.lines.append(str(line))

    def should_stop(self) -> bool:
        return False


def test_permission_manager_takes_a_mode_not_a_config():
    """Kesalahan aslinya: objek config dikirim sebagai mode."""
    from wth_ai.config import WTHConfig

    manager = PermissionManager()
    assert isinstance(manager.mode, PermissionMode)

    manager.set_mode("standard_testing")
    assert manager.mode is PermissionMode.STANDARD_TESTING
    assert manager.mode_sufficient(PermissionMode.READ_ONLY_RECON) is True

    # bukti bahwa mengirim config memang salah: mode jadi tidak bisa di-hash
    broken = PermissionManager.__new__(PermissionManager)
    broken.mode = WTHConfig.load()
    try:
        broken.mode_sufficient(PermissionMode.READ_ONLY_RECON)
    except TypeError as exc:
        assert "unhashable" in str(exc)
    else:  # pragma: no cover - kalau ini lewat, berarti bug-nya sudah tidak relevan
        raise AssertionError("mode non-enum seharusnya gagal di mode_sufficient")


def test_handler_builds_a_usable_permission_manager_and_scoped_bridge(monkeypatch):
    import wth_ai.jobs.handlers as handlers

    captured: dict = {}

    class _Bridge:
        def __init__(self, config, permission, scope, session_id, **kwargs):
            captured["permission"] = permission
            captured["scope"] = scope
            self.scope = scope

        def dispatch_tool(self, name, args, **kwargs):
            captured.setdefault("calls", []).append((name, args))
            raise AssertionError("probe tidak boleh dijalankan di test ini")

    monkeypatch.setattr(handlers, "AgentTerminalBridge", _Bridge, raising=False)
    # impor di dalam handler: patch modul sumbernya juga
    import wth_ai.terminal.bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "AgentTerminalBridge", _Bridge)
    monkeypatch.setattr(handlers, "handler_autopilot", handlers.handler_autopilot)

    # SessionStore palsu: tidak ada sesi tersimpan
    import wth_ai.session_store as store_mod

    class _Store:
        def __init__(self, *a, **k):
            pass

        def load_latest(self):
            return None

    monkeypatch.setattr(store_mod, "SessionStore", _Store)

    result = handlers.handler_autopilot(_Job(), _Ctx())

    permission = captured.get("permission")
    assert permission is not None, "handler tidak membuat bridge"
    assert isinstance(permission, PermissionManager)
    assert isinstance(permission.mode, PermissionMode), (
        f"mode harus PermissionMode, bukan {type(permission.mode).__name__}"
    )
    scope = captured.get("scope")
    assert scope is not None
    ok, reason = scope.is_in_scope("http://127.0.0.1:9999/x")
    assert ok, f"scope bridge tidak menerima targetnya sendiri: {reason}"
    assert result["type"] == "autopilot"


def test_handler_without_store_blocks_destructive_steps(monkeypatch):
    """Tanpa store, checkpoint tidak pernah benar-benar tersimpan.

    Katalog menaruh kandidat destruktif lebih dulu, jadi gerbang checkpoint
    menghentikan run SEBELUM probe pertama. Itu perilaku fail-closed yang
    diinginkan: tanpa checkpoint, efek destruktif tidak bisa direkonsiliasi
    setelah crash. Yang diuji di sini bukan "berapa banyak langkah aman yang
    sempat jalan" (nol), melainkan bahwa tidak ada satu pun request dikirim
    dan job berhenti dengan alasan yang bisa ditindaklanjuti.
    """
    import wth_ai.jobs.handlers as handlers
    import wth_ai.terminal.bridge as bridge_mod

    calls: list[dict] = []

    class _Bridge:
        def __init__(self, *a, **k):
            pass

        def dispatch_tool(self, name, args, **kwargs):
            calls.append(dict(args))
            return type("R", (), {
                "status_code": 200, "stdout": "ok",
                "transcript_id": "TX", "response_headers": {},
            })()

    class _Session:
        scope = type("S", (), {"destructive_allowed": True})()

    class _Store:
        def __init__(self, *a, **k):
            pass

        def load_latest(self):
            return _Session()

    class _Job:
        target = "127.0.0.1:9999"
        job_id = "J-nostore"
        metadata = {"authorized": True, "steps": 20, "cycles": 1}

    monkeypatch.setattr(bridge_mod, "AgentTerminalBridge", _Bridge)
    import wth_ai.session_store as store_mod

    monkeypatch.setattr(store_mod, "SessionStore", _Store)

    result = handlers.handler_autopilot(_Job(), _Ctx())
    assert calls == [], f"tidak boleh ada request tanpa checkpoint: {calls}"
    assert result["status"] == "stopped"
    assert result["checkpoint_error"]
    assert result["stop_incomplete"] is True


def test_handler_logs_recovery_states_distinctly(monkeypatch):
    """Job `completed` tidak boleh menyamarkan degradasi recovery.

    Tiga keadaan harus terpisah di log: checkpoint dimuat, resume ditolak,
    dan checkpoint gagal ditulis.
    """
    import wth_ai.jobs.handlers as handlers
    import wth_ai.terminal.bridge as bridge_mod
    from wth_ai.hunt.autopilot import AutopilotReport

    class _Bridge:
        def __init__(self, *a, **k):
            pass

        def dispatch_tool(self, *a, **k):
            raise AssertionError("tidak boleh ada probe di test ini")

    class _Store:
        def load_checkpoint(self, job_id):
            return {
                "schema_version": 2, "engine": "autopilot",
                "target": "https://foreign.tld", "cursor": 4,
                "steps": [], "engine_state": {},
            }

        def save_checkpoint(self, *a, **k):
            pass

    class _Manager:
        store = _Store()

    class _Ctx2(_Ctx):
        _manager = _Manager()

    report = AutopilotReport(target="127.0.0.1:9999")
    report.resume_rejected_reason = "target checkpoint 'https://foreign.tld' != target"
    report.checkpoint_error = "OSError: disk full"

    monkeypatch.setattr(bridge_mod, "AgentTerminalBridge", _Bridge)
    monkeypatch.setattr(
        "wth_ai.hunt.autopilot.run_autopilot",
        lambda **kwargs: report,
    )
    from wth_ai.session_store import SessionStore

    monkeypatch.setattr(SessionStore, "load_latest", lambda self: None)

    ctx = _Ctx2()
    result = handlers.handler_autopilot(_Job(), ctx)
    joined = "\n".join(ctx.lines)
    assert "recovery: checkpoint dimuat" in joined
    assert "recovery: resume ditolak" in joined
    assert "recovery: checkpoint gagal ditulis" in joined
    assert result["status"] == "completed"
    assert result["resume_rejected_reason"]
    assert result["checkpoint_error"]
