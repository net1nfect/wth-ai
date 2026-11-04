"""CLI: scan → verify → tamper gagal → gate CI."""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(args, tmp_path, extra=None):
    env = {**os.environ, "WTH_HOME": str(tmp_path / "home"),
           "WTH_SKIP_SETUP": "1", "WTH_PLAIN": "1"}
    env.update(extra or {})
    return subprocess.run([sys.executable, "wth-main.py", *args], cwd=REPO,
                          capture_output=True, text=True, timeout=180, env=env)


def test_scan_writes_all_artifacts(tmp_path):
    out = tmp_path / "out"
    proc = _run(["scan", "--offline-demo", "--out", str(out)], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "scan: 3 dicoba" in proc.stdout
    for name in ("run.json", "findings.sarif", "vulnerabilities.json",
                 "vulnerabilities.csv", "vulnerabilities/F-001.md"):
        assert (out / name).is_file(), name
    assert json.loads((out / "findings.sarif").read_text())["version"] == "2.1.0"


def test_verify_ok_then_tamper_fails(tmp_path):
    out = tmp_path / "out"
    _run(["scan", "--offline-demo", "--out", str(out)], tmp_path)
    ok = _run(["verify", str(out / "bundle")], tmp_path)
    assert ok.returncode == 0 and "OK" in ok.stdout

    findings = out / "bundle" / "findings.json"
    findings.write_text(findings.read_text().replace("F-001", "F-999"))
    bad = _run(["verify", str(out / "bundle")], tmp_path)
    assert bad.returncode == 1
    assert "berubah" in bad.stdout


def test_non_interactive_exits_two_on_findings(tmp_path):
    out = tmp_path / "out"
    proc = _run(["scan", "--offline-demo", "-n", "--fail-on", "high",
                 "--out", str(out)], tmp_path)
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)


def test_scan_mode_is_recorded(tmp_path):
    out = tmp_path / "out"
    _run(["scan", "--offline-demo", "--scan-mode", "quick", "--out", str(out)],
         tmp_path)
    assert json.loads((out / "run.json").read_text())["scan_mode"] == "quick"

def test_relative_out_is_resolved_against_caller_cwd(tmp_path):
    """`--out out/` harus mendarat di cwd pemanggil, bukan root repo."""
    env = {**os.environ, "WTH_HOME": str(tmp_path / "home"),
           "WTH_SKIP_SETUP": "1", "WTH_PLAIN": "1"}
    repo_out = REPO / "out"
    before = repo_out.exists()
    proc = subprocess.run(
        [sys.executable, str(REPO / "wth-main.py"), "scan", "--offline-demo",
         "--out", "out/"],
        cwd=tmp_path, capture_output=True, text=True, timeout=180, env=env)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "out" / "bundle" / "manifest.json").is_file()
    assert (tmp_path / "out" / "run.json").is_file()
    assert repo_out.exists() == before
