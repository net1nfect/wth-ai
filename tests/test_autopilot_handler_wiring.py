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


def test_handler_builds_a_usable_permission_manager_and_scoped_bridge(tmp_path, monkeypatch):
    import wth_ai.jobs.handlers as handlers

    # P1-1: handler wajib punya scope card valid sebelum membuat bridge.
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    from wth_ai.scope_card import ScopeCard

    ScopeCard(target="127.0.0.1:9999", domains=["127.0.0.1"], authorized=True).write(
        home / "scope_card.json"
    )

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
