"""CLI: ot read (dengan server palsu), browser capture, sandbox run."""

import os
import socket
import stat
import struct
import subprocess
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTERS = [11, 22, 33, 44]


def _run(args, cwd, extra_env=None):
    env = {**os.environ, "WTH_SKIP_SETUP": "1", "WTH_PLAIN": "1",
           "WTH_HOME": str(cwd / "home")}
    env.update(extra_env or {})
    return subprocess.run([sys.executable, str(REPO / "wth-ai-main.py"), *args],
                          cwd=cwd, capture_output=True, text=True, timeout=180,
                          env=env)


class _ModbusServer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]

    def run(self):
        conn, _ = self.sock.accept()
        with conn:
            data = conn.recv(1024)
            _tid, _pid, _len, unit = struct.unpack(">HHHB", data[:7])
            function, address, count = struct.unpack(">BHH", data[7:12])
            payload = b"".join(struct.pack(">H", REGISTERS[i])
                               for i in range(address, address + count))
            pdu = struct.pack(">BB", function, len(payload)) + payload
            conn.sendall(struct.pack(">HHHB", 1, 0, len(pdu) + 1, unit) + pdu)


def _start_server():
    server = _ModbusServer()
    server.start()
    return server


def test_ot_read_requires_confirm(tmp_path):
    proc = _run(["ot", "read", "--host", "127.0.0.1", "--cidr", "127.0.0.1/32"],
                tmp_path)
    assert proc.returncode == 2
    assert "butuh --confirm" in proc.stdout


def test_ot_read_denied_outside_allowlist(tmp_path):
    proc = _run(["ot", "read", "--host", "127.0.0.1", "--cidr", "10.0.0.0/8",
                 "--confirm"], tmp_path)
    assert proc.returncode == 2
    assert "EGRESS DENIED" in proc.stdout


def test_ot_read_success_with_local_server(tmp_path):
    server = _start_server()
    try:
        proc = _run(["ot", "read", "--host", "127.0.0.1",
                     "--cidr", "127.0.0.1/32", "--port", str(server.port),
                     "--address", "0", "--count", "3", "--confirm",
                     "--out", "out/ot.json"], tmp_path)
    finally:
        server.sock.close()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "values: 11, 22, 33" in proc.stdout
    assert "destructive=False" in proc.stdout
    assert (tmp_path / "out" / "ot.json").is_file()


def _fake_playwright(tmp_path: Path) -> Path:
    script = tmp_path / "fake-playwright"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import base64, pathlib, sys\n"
        "target = pathlib.Path(sys.argv[-1])\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "target.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUg=='))\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_browser_capture_cli_success(tmp_path):
    script = _fake_playwright(tmp_path)
    proc = _run(["browser", "capture", "http://lab/app", "--out", "shots",
                 "--name", "app"], tmp_path,
                {"WTH_PLAYWRIGHT_BIN": str(script)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "shots/app.png" in proc.stdout
    assert (tmp_path / "shots" / "app.png").is_file()


def test_browser_capture_cli_reports_missing_binary(tmp_path):
    proc = _run(["browser", "capture", "http://lab/app"], tmp_path,
                {"WTH_PLAYWRIGHT_BIN": str(tmp_path / "tidak-ada")})
    assert proc.returncode == 1
    assert "GAGAL" in proc.stdout
    assert "playwright install" in proc.stdout


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "docker-argv.txt"
    script = tmp_path / "docker"
    script.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$FAKE_DOCKER_LOG\"\nexit 0\n")
    script.chmod(0o755)
    return script, log


def test_sandbox_run_requires_yes(tmp_path):
    proc = _run(["sandbox", "run", "--", "sh", "-lc", "true"], tmp_path)
    assert proc.returncode == 2
    assert "butuh --yes" in proc.stdout
    assert "docker run" in proc.stdout


def test_sandbox_run_executes_with_yes(tmp_path):
    script, log = _fake_docker(tmp_path)
    bin_dir = script.parent
    proc = _run(["sandbox", "run", "--yes", "--mount", "/tmp:/data:ro",
                 "--", "sh", "-lc", "true"], tmp_path,
                {"PATH": f"{bin_dir}:{os.environ['PATH']}",
                 "FAKE_DOCKER_LOG": str(log)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    argv = log.read_text().split("\n")
    assert "run" in argv
    assert "--read-only" in argv
    assert "/tmp:/data:ro" in argv