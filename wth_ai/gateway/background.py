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


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_gateway_pid() -> int | None:
    path = gateway_pid_path()
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def cleanup_stale_runtime() -> bool:
    """Remove pid/socket files when the owning process is gone."""
    pid = read_gateway_pid()
    if pid and is_process_alive(pid):
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
    pid = read_gateway_pid()
    if pid and is_process_alive(pid):
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
    pid = read_gateway_pid()
    alive = bool(pid and is_process_alive(pid))
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
    pid = read_gateway_pid()
    if not pid:
        cleanup_stale_runtime()
        return False
    if not is_process_alive(pid):
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
        if not is_process_alive(pid):
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
