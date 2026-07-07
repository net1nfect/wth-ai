"""Gateway background daemon lifecycle tests."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from wth_ai.gateway.background import (
    cleanup_stale_runtime,
    gateway_status_info,
    is_gateway_running,
    read_gateway_pid,
    stop_gateway,
    write_gateway_status,
)
from wth_ai.gateway.rpc import gateway_pid_path, gateway_socket_path


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    monkeypatch.setenv("WTH_TEST_MODE", "1")


def _hold_socket(path) -> socket.socket:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    return sock


def test_cleanup_stale_pid(tmp_path):
    pid_path = gateway_pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("999999999", encoding="utf-8")
    assert cleanup_stale_runtime() is True
    assert not pid_path.exists()


def test_is_gateway_running_with_live_socket(monkeypatch):
    short_home = Path("/tmp") / f"wth-gw-{os.getpid()}"
    short_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WTH_HOME", str(short_home))
    pid_path = gateway_pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    holder = _hold_socket(gateway_socket_path())
    try:
        assert is_gateway_running() is True
    finally:
        holder.close()
        gateway_socket_path().unlink(missing_ok=True)
        gateway_pid_path().unlink(missing_ok=True)


def test_gateway_status_info_not_running():
    info = gateway_status_info()
    assert info["running"] is False
    assert info["pid"] is None


def test_stop_gateway_clears_stale_pid():
    pid_path = gateway_pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("999999999", encoding="utf-8")
    assert stop_gateway() is True
    assert read_gateway_pid() is None


def test_stop_gateway_sends_sigterm(monkeypatch):
    pid_path = gateway_pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("424242", encoding="utf-8")
    killed: list[int] = []

    def fake_kill(pid: int, sig: int) -> None:
        killed.append(pid)
        if sig == 15:
            raise ProcessLookupError

    monkeypatch.setattr(os, "kill", fake_kill)
    assert stop_gateway() is True
    assert 424242 in killed


def test_write_gateway_status_file():
    write_gateway_status("running", foreground=False)
    info = gateway_status_info()
    assert info["recorded"]["state"] == "running"


def test_spawn_background_raises_when_running(monkeypatch):
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    with patch("wth_ai.gateway.background.is_gateway_running", return_value=True):
        with patch("wth_ai.gateway.background.read_gateway_pid", return_value=42):
            from wth_ai.gateway.background import spawn_background

            with pytest.raises(RuntimeError, match="already running"):
                spawn_background("config/wth_config.yaml")


def test_gateway_cli_stop_and_status_registered():
    import importlib.util
    from pathlib import Path

    from click.testing import CliRunner

    path = Path(__file__).resolve().parents[1] / "wth-main.py"
    spec = importlib.util.spec_from_file_location("wth_main", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    runner = CliRunner()
    result = runner.invoke(mod.cli, ["gateway", "--help"])
    assert result.exit_code == 0
    assert "stop" in result.output
    assert "run" in result.output
