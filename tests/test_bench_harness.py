"""Harness benchmark offline: skor precision/recall terhadap ground truth.

Benchmark tidak boleh menyentuh internet: tiap task menyajikan respons dari
server palsu di 127.0.0.1, dan truth dibandingkan dengan temuan yang benar-benar
didapat loop — bukan dengan skrip yang "sudah tahu jawabannya".
"""

from __future__ import annotations

import http.client
import json
import os
import pathlib
import subprocess
import sys

import pytest
from wth_ai.bench.harness import BenchError, run_task, score, task_dirs

REPO = pathlib.Path(__file__).resolve().parents[1]

def _task(root: pathlib.Path, name: str, *, findings: list[dict], truth: list[dict],
          responses: dict[str, dict] | None = None, write_truth: bool = True) -> pathlib.Path:
    task = root / name
    task.mkdir(parents=True)
    (task / "task.json").write_text(json.dumps({
        "name": name,
        "findings": findings,
        "responses": responses or {"/api/v1/orders/1": {"status": 200, "body": '{"owner":"other"}'}},
    }))
    if write_truth:
        (task / "ground_truth.json").write_text(json.dumps({"findings": truth}))
    return task

_IDOR_TRUTH = [{"vuln_class": "idor", "path": "/api/v1/orders/1"}]

def _idor(path: str = "/api/v1/orders/1", fid: str = "F-001") -> dict:
    return {"id": fid, "hypothesis": f"IDOR GET {path}", "vuln_class": "idor",
            "path": path, "severity": "high"}

def _port(url: str) -> int:
    return int(url.rsplit(":", 1)[1].split("/")[0])

def test_task_with_matching_finding_scores_perfect(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "tasks"
    _task(root, "idor-orders", findings=[_idor()], truth=_IDOR_TRUTH)

    result = run_task(root / "idor-orders")

    assert result.task == "idor-orders"
    assert result.passed is True
    assert result.ground_truth_hits == 1
    assert [f["vuln_class"] for f in result.findings] == ["idor"]
    assert result.duration_s >= 0.0

    totals = score([result])
    assert totals["precision"] == 1.0
    assert totals["recall"] == 1.0

def test_extra_finding_lowers_precision(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "tasks"
    _task(root, "idor-plus-noise",
          findings=[_idor(), _idor(path="/api/v1/nope", fid="F-002")],
          truth=_IDOR_TRUTH,
          responses={
              "/api/v1/orders/1": {"status": 200, "body": '{"owner":"other"}'},
              "/api/v1/nope": {"status": 200, "body": '{"ok":true}'},
          })

    result = run_task(root / "idor-plus-noise")

    assert result.passed is False
    assert result.ground_truth_hits == 1
    totals = score([result])
    assert totals["precision"] == 0.5
    assert totals["recall"] == 1.0

def test_missing_ground_truth_is_explicit_error(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "tasks"
    task = _task(root, "no-truth", findings=[_idor()], truth=[], write_truth=False)

    with pytest.raises(BenchError, match="ground_truth\\.json"):
        run_task(task)

def test_scores_aggregate_across_tasks(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "tasks"
    _task(root, "a", findings=[_idor()], truth=_IDOR_TRUTH)
    _task(root, "b", findings=[_idor(path="/api/v1/orders/2", fid="F-009")],
          truth=[{"vuln_class": "idor", "path": "/api/v1/orders/2"}],
          responses={"/api/v1/orders/2": {"status": 200, "body": '{"owner":"other"}'}})

    results = [run_task(d) for d in task_dirs(root)]

    totals = score(results)
    assert totals["tasks"] == 2
    assert totals["passed"] == 2
    assert totals["precision"] == 1.0
    assert totals["recall"] == 1.0

def test_task_servers_bind_loopback_only(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "tasks"
    task = _task(root, "loopback", findings=[_idor()], truth=_IDOR_TRUTH)

    result = run_task(task)

    assert all(url.startswith("http://127.0.0.1:") for url in result.targets)
    # Server sudah dimatikan setelah task: port tidak boleh lagi melayani.
    with pytest.raises(OSError):
        http.client.HTTPConnection("127.0.0.1", _port(result.targets[0]), timeout=1) \
            .request("GET", "/api/v1/orders/1")

def _cli(args: list[str], home: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "WTH_HOME": str(home), "WTH_SKIP_SETUP": "1",
           "WTH_PLAIN": "1"}
    return subprocess.run([sys.executable, "wth-ai-main.py", *args], cwd=REPO,
                          capture_output=True, text=True, timeout=180, env=env)

def test_cli_exit_codes(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "tasks"
    _task(root, "green", findings=[_idor()], truth=_IDOR_TRUTH)
    out = tmp_path / "out"

    ok = _cli(["bench", "--tasks", str(root), "--out", str(out)], tmp_path / "home")
    assert ok.returncode == 0, ok.stderr + ok.stdout
    payload = json.loads((out / "bench.json").read_text())
    assert payload["totals"]["passed"] == 1

    _task(root, "red", findings=[_idor(path="/api/v1/nope", fid="F-777")],
          truth=_IDOR_TRUTH,
          responses={"/api/v1/nope": {"status": 200, "body": "{}"}})
    bad = _cli(["bench", "--tasks", str(root), "--out", str(out)], tmp_path / "home")
    assert bad.returncode == 1, bad.stderr + bad.stdout
