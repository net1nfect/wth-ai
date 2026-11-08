"""Background gateway process management (Unix double-fork daemonize)."""

from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.gateway.rpc import gateway_pid_path, gateway_socket_path
from wth_ai.workspace import wth_home


def gateway_log_path() -> Path:
    return wth_home() / "logs" / "gateway.log"


def gateway_status_path() -> Path:
    return wth_home() / "runtime" / "gateway.status.json"


def _proc_state_ticks(pid: int) -> int | None:
    """Start time (clock ticks) of a process from /proc/<pid>/stat.

    Field 22 (starttime) of the stat format. `comm` (field 2) may contain
    spaces/parens, so we split after the last ')' rather than by whitespace
    from the left.
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            data = fh.read()
    except (OSError, ValueError):
        return None
    idx = data.rfind(")")
    if idx < 0:
        return None
    fields = data[idx + 2:].split()
    # After ') ' the first token is state (field 3); starttime (field 22)
    # is therefore at local index 19.
    if len(fields) <= 19:
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def _gateway_identity() -> tuple[int, int | None] | None:
    """Return (pid, start_ticks) recorded in the pid file, or None."""
    path = gateway_pid_path()
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    parts = text.splitlines()
    if not parts:
        return None
    try:
        pid = int(parts[0].strip())
    except ValueError:
        return None
    start: int | None = None
    if len(parts) >= 2 and parts[1].strip():
        try:
            start = int(parts[1].strip())
        except ValueError:
            start = None
    return pid, start


def write_gateway_pid(pid: int) -> None:
    """Write pid file as `<pid>\\n<start_ticks>\\n`.

    The second line records the process start time so later liveness checks
    can detect PID reuse (P2-1): a live PID whose start time differs is a
    different process and must not be signalled.
    """
    path = gateway_pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    start = _proc_state_ticks(pid)
    text = f"{pid}\n" + (f"{start}\n" if start is not None else "")
    path.write_text(text, encoding="utf-8")


def is_process_alive(pid: int, *, expected_start: int | None = None) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if expected_start is not None:
        actual = _proc_state_ticks(pid)
        if actual is None or actual != expected_start:
            return False
    return True


def read_gateway_pid() -> int | None:
    path = gateway_pid_path()
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return None


def cleanup_stale_runtime() -> bool:
    """Remove pid/socket files when the owning process is gone."""
    ident = _gateway_identity()
    if ident:
        pid, start = ident
        if is_process_alive(pid, expected_start=start):
            return False
    removed = False
    for path in (gateway_pid_path(), gateway_socket_path()):
        if path.exists():
            try:
                path.unlink()
                removed = True
            except OSError:
                pass
    return removed


def is_gateway_running() -> bool:
    cleanup_stale_runtime()
    ident = _gateway_identity()
    if ident:
        pid, start = ident
        if is_process_alive(pid, expected_start=start):
            sock = gateway_socket_path()
            return sock.exists() and sock.is_socket()
    return False


def write_gateway_status(state: str, **extra: Any) -> None:
    path = gateway_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state,
        "pid": os.getpid(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "socket": str(gateway_socket_path()),
        "log": str(gateway_log_path()),
        **extra,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def gateway_status_info() -> dict[str, Any]:
    cleanup_stale_runtime()
    ident = _gateway_identity()
    alive = False
    pid: int | None = None
    if ident:
        pid, start = ident
        alive = is_process_alive(pid, expected_start=start)
    sock = gateway_socket_path()
    info: dict[str, Any] = {
        "running": alive and sock.is_socket() if sock.exists() else False,
        "pid": pid,
        "socket": str(sock),
        "log": str(gateway_log_path()),
        "status_file": str(gateway_status_path()),
    }
    if gateway_status_path().exists():
        try:
            info["recorded"] = json.loads(gateway_status_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    if info["running"]:
        try:
            from wth_ai.gateway.client import GatewayClient

            info["rpc"] = GatewayClient().gateway_status()
        except Exception as exc:  # noqa: BLE001
            info["rpc_error"] = str(exc)
    return info


def assert_not_running() -> None:
    if os.environ.get("WTH_TEST_MODE") == "1":
        return
    if is_gateway_running():
        pid = read_gateway_pid()
        raise RuntimeError(f"gateway already running (pid {pid})")


def wait_for_gateway(*, timeout: float = 15.0, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_gateway_running():
            return True
        time.sleep(interval)
    return False


def redirect_stdio(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    if fd > 2:
        os.close(fd)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    if devnull > 2:
        os.close(devnull)


def spawn_background(config_path: str) -> int:
    """Double-fork daemonize and start the gateway in the grandchild."""
    if not hasattr(os, "fork"):
        raise RuntimeError("background mode requires Unix fork (use --foreground on Windows)")

    assert_not_running()
    log_path = gateway_log_path()

    pid = os.fork()
    if pid > 0:
        if wait_for_gateway():
            return read_gateway_pid() or pid
        raise RuntimeError("gateway failed to start — check logs/gateway.log")

    os.setsid()
    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)

    redirect_stdio(log_path)
    os.chdir(str(wth_home()))
    os.umask(0o027)

    from wth_ai.config import WTHConfig
    from wth_ai.gateway.daemon import GatewayDaemon
    from wth_ai.workspace import init_workspace

    init_workspace()
    GatewayDaemon(WTHConfig.load(config_path)).start(foreground=True)
    os._exit(0)


def stop_gateway(*, timeout: float = 10.0) -> bool:
    """Send SIGTERM to the gateway PID and wait for exit."""
    ident = _gateway_identity()
    if not ident:
        cleanup_stale_runtime()
        return False
    pid, start = ident
    if not is_process_alive(pid, expected_start=start):
        cleanup_stale_runtime()
        write_gateway_status("stopped", reason="stale pid")
        return True

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        cleanup_stale_runtime()
        return True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_process_alive(pid, expected_start=start):
            cleanup_stale_runtime()
            write_gateway_status("stopped", reason="sigterm")
            return True
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    cleanup_stale_runtime()
    write_gateway_status("stopped", reason="sigkill")
    return True
