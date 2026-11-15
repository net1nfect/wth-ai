"""Clean-room runner (GAP-007 versi tanpa-Docker, keputusan operator).

Di-deklarasi JUJUR: ini bukan jail kernel. Yang diverifikasi di sini:
  * cwd/HOME/TMPDIR child = directory throwaway di WTH_HOME/cleanroom
  * env `WTH_*` dan turunan host lain tidak bocor ke child
  * eksekusi nyata bekerja, output tertangkapw, exit code benar
"""

from __future__ import annotations

import os

import pytest

from wth_ai.executor.cleanroom import CleanRoom


def test_cleanroom_basic_execution_and_capture():
    room = CleanRoom.create(label="t-basic")
    try:
        out, err, code = room.run("echo arena-hello && echo boom >&2; exit 3")
        assert code == 3
        assert "arena-hello" in out
        assert "boom" in err
    finally:
        room.cleanup()


def test_cleanroom_env_is_stripped_and_home_isolation():
    os.environ["WTH_SENTINEL_SECRET"] = "should-not-leak"
    room = CleanRoom.create(label="t-env")
    try:
        out, _, _ = room.run(
            'python3 -c "import os,sys; print(os.environ.get(\'WTH_SENTINEL_SECRET\',\'ABSENT\'));'
            "print(os.getcwd()); print(os.environ.get('HOME'))\""
        )
        lines = out.strip().splitlines()
        assert lines[0] == "ABSENT", f"env WTH_* bocor ke child: {lines[0]}"
        # cwd & HOME berada DI DALAM room, bukan di host
        root = str(room.root)
        assert lines[1].startswith(root), f"cwd child di luar room: {lines[1]}"
        assert lines[2].startswith(root), f"HOME child di luar room: {lines[2]}"
    finally:
        room.cleanup()
        os.environ.pop("WTH_SENTINEL_SECRET", None)


def test_cleanroom_home_writes_do_not_reach_host_home():
    room = CleanRoom.create(label="t-home")
    try:
        room.run('echo wth-cleanroom-probe > "$HOME/wth_probe.txt"')
        assert not os.path.exists(os.path.join(os.path.expanduser("~"), "wth_probe.txt"))
        assert (room.home / "wth_probe.txt").exists()
    finally:
        room.cleanup()


def test_cleanroom_timeout_maps_to_124():
    room = CleanRoom.create(label="t-timeout")
    try:
        out, err, code = room.run("sleep 30", timeout=1)
        assert code == 124
        assert "timeout" in err.lower()
    finally:
        room.cleanup()


def test_bridge_run_shell_cleanroom(tmp_path):
    """Jalur broker/bridge: run_shell(cleanroom=True) tetap masuk transcript."""
    from wth_ai.config import WTHConfig
    from wth_ai.models import PermissionMode
    from wth_ai.permission_manager import PermissionManager
    from wth_ai.scope_manager import ScopeManager
    from wth_ai.terminal.bridge import AgentTerminalBridge

    cfg = WTHConfig.load()
    scope = ScopeManager()
    scope.set_scope(target="example.com", authorized=True)
    bridge = AgentTerminalBridge(cfg, PermissionManager(PermissionMode.FULL_LAB), scope, "cleanroom-test")
    result = bridge.run_shell("cat /etc/hostname", cleanroom=True)
    assert result.blocked is False
    assert result.stdout.strip()  # stdout non-empty: cat hostname sukses di clean-room
    # tidak leaving room yang bersisa (cleanup sudah dipanggil).
    from wth_ai.workspace import wth_home

    cleanroot = wth_home() / "cleanroom"
    leftovers = [d for d in cleanroot.glob("shell-*") if d.is_dir()] if cleanroot.exists() else []
    assert leftovers == [], f"cleanroom tidak dibersihkan: {leftovers}"
