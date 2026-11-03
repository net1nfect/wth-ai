"""Regression tests — F1/WU-106 remainder: `RunningActionRegistry` + kill process-group.

Bug asli (WORK-UNITS.md WU-106 scope decision): interrupt-check in-process
(`ctx.stopped`, WU-106 partial) sudah ada, TAPI mekanisme membunuh PROSES nyata
yang sudah telanjur berjalan (`os.killpg`) TIDAK ADA — proses yang di-spawn
`run_shell` bisa jadi orphan (temuan WU-108: "membunuh PROSES nyata butuh
RunningActionRegistry, di luar scope WU ini").

Fix: `RunningActionRegistry` (track pid+pgid) + `start_new_session=True` di
`shell_runner` + `kill_all()` bertingkat SIGTERM→SIGKILL, diwire ke dua
permukaan kill-switch nyata: `SupervisorAgent.stop()` (`/stop`) dan
`WTHOrchestrator.shutdown()` (`/quit`, ctrl+c).

Invariant yang dijaga test:
* kill_all TIDAK PERNAH menyentuh process-group milik proses ini sendiri
  (guard `os.getpgid(0)`) — salah-registrasi tidak boleh jadi bunuh diri.
* kill_all menjangkau SELURUH tree (anak/cucu) lewat process-group, bukan cuma
  proses langsung.
* `shell_runner` register saat spawn & unregister di finally (tanpa leak entry),
  dan `start_new_session=True` membuat pgid anak == pid-nya.
* kill_all tidak pernah raise — cleanup shutdown tidak boleh crash.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_PLAIN", "1")

from wth_ai.running_actions import (  # noqa: E402
    RunningActionRegistry,
    get_running_actions,
    reset_running_actions,
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _spawn_sleep(seconds: int = 30) -> subprocess.Popen:
    return subprocess.Popen(
        ["sleep", str(seconds)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class TestRegistryLifecycle:
    def test_register_snapshot_unregister(self):
        reg = RunningActionRegistry()
        aid = reg.register(12345, pgid=12345, label="nmap -sV", actor="executor")
        snap = reg.snapshot()
        assert len(snap) == 1
        assert snap[0]["action_id"] == aid
        assert snap[0]["pid"] == 12345 and snap[0]["pgid"] == 12345
        assert snap[0]["label"] == "nmap -sV"
        assert snap[0]["actor"] == "executor"
        assert reg.active_count() == 1
        assert reg.unregister(aid) is True
        assert reg.active_count() == 0
        assert reg.unregister(aid) is False  # sudah tidak ada -> False

    def test_register_defaults_pgid_to_pid(self):
        reg = RunningActionRegistry()
        aid = reg.register(111, label="x")
        entry = next(e for e in reg.snapshot() if e["action_id"] == aid)
        assert entry["pgid"] == 111

    def test_register_ignores_invalid_pid(self):
        reg = RunningActionRegistry()
        assert reg.register(0, label="bogus") is None
        assert reg.register(-5, label="bogus") is None
        assert reg.active_count() == 0


class TestKillAll:
    def test_kill_all_kills_a_real_process_group(self):
        reg = RunningActionRegistry()
        proc = _spawn_sleep()
        try:
            reg.register(proc.pid, label="sleep-30", actor="test")
            report = reg.kill_all(grace_seconds=1.5)
            assert proc.pid in [t["pid"] for t in report["terminated"]], report
            proc.wait(timeout=5)  # reap
            assert not _alive(proc.pid)
            assert reg.active_count() == 0
            assert report["errors"] == []
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_kill_all_reaches_grandchildren_via_process_group(self):
        """Cucu (child dari child) hanya terjangkau lewat process-group kill."""
        reg = RunningActionRegistry()
        parent = subprocess.Popen(
            [
                "python3",
                "-c",
                "import subprocess,time; p=subprocess.Popen(['sleep','30']); "
                "print(p.pid, flush=True); time.sleep(30)",
            ],
            start_new_session=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert parent.stdout is not None
            child_pid = int(parent.stdout.readline().strip())
            reg.register(parent.pid, label="tree")
            report = reg.kill_all(grace_seconds=1.5)
            parent.wait(timeout=5)
            deadline = time.monotonic() + 3
            while _alive(child_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not _alive(parent.pid), report
            assert not _alive(child_pid), f"cucu masih hidup: {child_pid}"
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait()
            try:
                os.kill(child_pid, 9)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    def test_kill_all_never_targets_own_process_group(self):
        reg = RunningActionRegistry()
        own_pgid = os.getpgid(0)
        aid = reg.register(os.getpid(), pgid=own_pgid, label="self-bogus")
        report = reg.kill_all(grace_seconds=0.2)
        assert own_pgid in [s["pgid"] for s in report["skipped"]], report
        assert aid in [s["action_id"] for s in report["skipped"]]
        # entry tetap terdaftar (bukan di-unregister diam-diam) — operator bisa periksa
        assert reg.active_count() == 1
        reg.unregister(aid)

    def test_kill_all_treats_already_dead_as_terminated(self):
        reg = RunningActionRegistry()
        proc = subprocess.Popen(["true"], start_new_session=True)
        proc.wait()
        reg.register(proc.pid, label="already-dead")
        report = reg.kill_all(grace_seconds=0.2)
        assert reg.active_count() == 0
        assert report["errors"] == []
        assert report["terminated"]

    def test_kill_all_empty_registry_is_noop(self):
        reg = RunningActionRegistry()
        report = reg.kill_all()
        assert report == {"terminated": [], "skipped": [], "errors": []}


class TestShellRunnerIntegration:
    def test_spawn_registers_then_unregisters(self):
        from wth_ai.shell_runner import run_command

        reset_running_actions()
        seen: dict = {}

        def _on_line(line: str) -> None:
            snap = get_running_actions().snapshot()
            seen["count"] = len(snap)
            seen["label"] = snap[0]["label"] if snap else ""

        out, _err, code = run_command("echo hello", timeout=10, on_line=_on_line)
        assert code == 0 and "hello" in out
        assert seen.get("count") == 1, seen
        assert "echo" in seen.get("label", "")
        assert get_running_actions().active_count() == 0
        reset_running_actions()

    def test_live_path_spawn_uses_own_process_group(self):
        """pgid anak == pid anak (start_new_session) -> killpg(pid) = satu tree."""
        from wth_ai.shell_runner import run_command

        reset_running_actions()
        observed: dict = {}

        def _on_line(line: str) -> None:
            snap = get_running_actions().snapshot()
            if snap:
                observed["pid"] = snap[0]["pid"]
                observed["pgid"] = os.getpgid(snap[0]["pid"])

        run_command(
            "python3 -c \"print('x', flush=True); import time; time.sleep(0.3)\"",
            timeout=10,
            on_line=_on_line,
        )
        assert observed, "tidak ada proses ter-registrasi selama run"
        assert observed["pgid"] == observed["pid"]
        assert get_running_actions().active_count() == 0
        reset_running_actions()

    def test_timeout_still_returns_124_and_cleans_registry(self):
        from wth_ai.shell_runner import run_command

        reset_running_actions()
        out, err, code = run_command("sleep 30", timeout=1, on_line=lambda line: None)
        assert code == 124 and "timeout after 1s" in err, (out, err, code)
        assert get_running_actions().active_count() == 0
        reset_running_actions()

    def test_non_live_path_cleans_registry(self):
        from wth_ai.shell_runner import run_command

        reset_running_actions()
        out, err, code = run_command("echo hi", timeout=10)
        assert code == 0 and "hi" in out
        assert get_running_actions().active_count() == 0
        reset_running_actions()


class TestKillSwitchSurfaces:
    def test_supervisor_stop_kills_registered_processes(self):
        from unittest.mock import MagicMock

        from wth_ai.agents.supervisor import SupervisorAgent
        from wth_ai.config import WTHConfig

        reset_running_actions()
        proc = _spawn_sleep()
        try:
            get_running_actions().register(proc.pid, label="lingering")
            sup = SupervisorAgent(WTHConfig(), client=MagicMock())
            out = sup.stop()
            assert "stopped" in out.lower()
            proc.wait(timeout=5)
            assert not _alive(proc.pid)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            reset_running_actions()

    def test_orchestrator_shutdown_kills_lingering_processes(self):
        from wth_ai.config import WTHConfig
        from wth_ai.orchestrator import WTHOrchestrator

        reset_running_actions()
        proc = _spawn_sleep()
        try:
            get_running_actions().register(proc.pid, label="lingering-at-shutdown")
            orch = WTHOrchestrator(WTHConfig(), fresh=True)
            msg = orch.shutdown(reason="test")
            proc.wait(timeout=5)
            assert not _alive(proc.pid)
            assert "dihentikan" in msg, msg
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            reset_running_actions()


class TestKillSwitchCoverageExtensions:
    """F1 lanjutan: recon/runner + rpc/execute_server ikut ter-registrasi."""

    def test_recon_run_bounded_registers_during_run_and_cleans_after(self):
        from types import SimpleNamespace

        from wth_ai.recon.runner import SafeToolRunner

        reset_running_actions()
        fake_self = SimpleNamespace(
            _spawn=lambda argv, **kw: subprocess.Popen(argv, **kw),
            timeout=10,
            raw_dir=None,
        )
        result: list = []
        thread = threading.Thread(
            target=lambda: result.append(SafeToolRunner._run_bounded(fake_self, ["sleep", "0.5"]))
        )
        thread.start()
        seen = 0
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            seen = get_running_actions().active_count()
            if seen:
                break
            time.sleep(0.01)
        assert seen == 1, f"recon spawn tidak ter-registrasi (count={seen})"
        snap = get_running_actions().snapshot()
        assert snap[0]["label"].startswith("recon:")
        thread.join(timeout=10)
        assert result and result[0][2] == 0
        assert get_running_actions().active_count() == 0
        reset_running_actions()

    def test_recon_timeout_kills_whole_process_tree(self):
        from types import SimpleNamespace

        from wth_ai.recon.runner import SafeToolRunner

        reset_running_actions()
        fake_self = SimpleNamespace(
            _spawn=lambda argv, **kw: subprocess.Popen(argv, **kw),
            timeout=1,
            raw_dir=None,
        )
        cmd = [
            "python3", "-c",
            "import subprocess,time; p=subprocess.Popen(['sleep','30']); print(p.pid, flush=True); time.sleep(30)",
        ]
        got: dict = {}

        def _run() -> None:
            try:
                SafeToolRunner._run_bounded(fake_self, cmd)
            except subprocess.TimeoutExpired:
                got["timeout"] = True

        thread = threading.Thread(target=_run)
        thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "pid" not in got:
            snap = get_running_actions().snapshot()
            if snap:
                got["pid"] = snap[0]["pid"]
            time.sleep(0.01)
        assert "pid" in got, "proses recon tidak ter-registrasi sebelum timeout"
        thread.join(timeout=15)
        assert got.get("timeout"), "timeout tidak ter-raise"
        assert get_running_actions().active_count() == 0
        # seluruh group benar-benar hilang (parent di-reap, cucu di-kill)
        gone = False
        end = time.monotonic() + 5
        while time.monotonic() < end:
            try:
                os.killpg(got["pid"], 0)
            except ProcessLookupError:
                gone = True
                break
            time.sleep(0.05)
        assert gone, "process-group masih ada setelah timeout kill"
        reset_running_actions()

    def test_rpc_execute_registers_and_cleans(self):
        from wth_ai.models import PermissionMode
        from wth_ai.rpc.execute_server import ExecuteRpcServer

        reset_running_actions()
        srv = ExecuteRpcServer()
        srv.scope.set_scope(target="example.com", authorized=True, in_scope=["example.com"])
        srv.perms.set_mode(PermissionMode.STANDARD_TESTING)
        result: list = []
        thread = threading.Thread(
            target=lambda: result.append(
                srv.execute({"target": "example.com", "code": "import time; time.sleep(0.6); print('ok')"})
            )
        )
        thread.start()
        seen = 0
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            seen = get_running_actions().active_count()
            if seen:
                break
            time.sleep(0.01)
        assert seen == 1, f"rpc code-exec tidak ter-registrasi (count={seen})"
        assert get_running_actions().snapshot()[0]["label"] == "rpc:code-exec"
        thread.join(timeout=15)
        assert result and result[0]["status"] == "success", result
        assert get_running_actions().active_count() == 0
        reset_running_actions()
