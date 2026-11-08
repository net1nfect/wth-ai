"""OT/Modbus: recon hanya baca, write ditolak, dan tidak ada koneksi nyata."""

import os
import subprocess
import sys
from pathlib import Path

from wth_ai.ot import modbus
from wth_ai.ot.modbus import (DestructiveBlocked, READ_FUNCTIONS, WRITE_FUNCTIONS,
                              build_read_request, build_write_request, plan_reads)


def test_read_request_is_non_destructive():
    request = build_read_request("172.28.0.10", "holding", 0, count=10)
    assert request.destructive is False
    assert request.function_code in READ_FUNCTIONS
    assert "--read-only" in request.argv()
    assert "modbus-read" == request.argv()[0]


def test_write_is_blocked():
    try:
        build_write_request("172.28.0.10", "holding", 0, value=1)
    except DestructiveBlocked as exc:
        assert "tidak diizinkan" in str(exc)
    else:
        raise AssertionError("write seharusnya ditolak")


def test_no_write_function_code_is_reachable():
    assert not (READ_FUNCTIONS & WRITE_FUNCTIONS)
    for kind in modbus.KINDS:
        assert modbus.KINDS[kind] in READ_FUNCTIONS


def test_plan_reads_returns_only_read_requests():
    requests = plan_reads("172.28.0.10", unit=2)
    assert requests
    assert all(request.destructive is False for request in requests)
    assert {request.unit for request in requests} == {2}


def test_cli_ot_plan_prints_reads_only(tmp_path):
    import os
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "WTH_SKIP_SETUP": "1", "WTH_PLAIN": "1",
           "WTH_HOME": str(tmp_path / "home")}
    proc = subprocess.run([sys.executable, str(repo / "wth-ai-main.py"), "ot", "plan",
                           "--host", "172.28.0.10", "--unit", "2",
                           "--block", "holding:0:4"],
                          cwd=tmp_path, capture_output=True, text=True,
                          timeout=180, env=env)
    assert proc.returncode == 0, proc.stderr
    assert "1 permintaan baca" in proc.stdout
    assert "destructive=False" in proc.stdout
    assert "tidak ada koneksi dibuka" in proc.stdout


def test_cli_ot_plan_rejects_bad_block(tmp_path):
    import os
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "WTH_SKIP_SETUP": "1", "WTH_PLAIN": "1",
           "WTH_HOME": str(tmp_path / "home")}
    proc = subprocess.run([sys.executable, str(repo / "wth-ai-main.py"), "ot", "plan",
                           "--host", "172.28.0.10", "--block", "holding-0-4"],
                          cwd=tmp_path, capture_output=True, text=True,
                          timeout=180, env=env)
    assert proc.returncode != 0
    assert "tidak valid" in (proc.stdout + proc.stderr)


def test_bad_inputs_are_rejected():
    for kwargs in ({"kind": "write_coil", "address": 0},
                   {"kind": "holding", "address": -1},
                   {"kind": "holding", "address": 0, "count": 0},
                   {"kind": "holding", "address": 0, "unit": 300}):
        kind = kwargs.pop("kind")
        try:
            build_read_request("172.28.0.10", kind, **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"input seharusnya ditolak: {kind} {kwargs}")