"""CLI KB: build dari file mentah, search FTS, status setelah dibangun."""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

NVD = {"vulnerabilities": [{"cve": {
    "id": "CVE-2021-44228", "published": "2021-12-10T00:00:00",
    "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 10.0}}]},
    "weaknesses": [{"description": [{"value": "CWE-502"}]}],
    "configurations": [{"nodes": [{"cpeMatch": [
        {"criteria": "cpe:2.3:a:apache:log4j:2.14.1:*:*:*:*:*:*:*"}]}]}],
    "descriptions": [{"lang": "en", "value": "Log4Shell JNDI remote code execution"}],
}}]}


def _run(args, home, cwd=None):
    env = {**os.environ, "WTH_HOME": str(home), "WTH_SKIP_SETUP": "1",
           "WTH_PLAIN": "1"}
    return subprocess.run([sys.executable, str(REPO / "wth-ai-main.py"), *args],
                          cwd=cwd or REPO, capture_output=True, text=True,
                          timeout=180, env=env)


def _source(tmp_path):
    source = tmp_path / "src"
    (source / "nvd").mkdir(parents=True)
    (source / "nvd" / "part.json").write_text(json.dumps(NVD), encoding="utf-8")
    return source


def test_build_then_status_and_search(tmp_path):
    source = _source(tmp_path)
    home = tmp_path / "home"

    built = _run(["kb", "build", "--source", str(source)], home, cwd=tmp_path)
    assert built.returncode == 0, built.stderr
    assert "1 record" in built.stdout and "1 siap-assessment" in built.stdout

    status = _run(["kb", "status"], home, cwd=tmp_path)
    assert "1 record" in status.stdout
    assert "belum pernah" not in status.stdout

    found = _run(["kb", "search", "log4shell"], home, cwd=tmp_path)
    assert found.returncode == 0, found.stdout
    assert "CVE-2021-44228" in found.stdout


def test_search_without_hit_exits_1(tmp_path):
    source = _source(tmp_path)
    home = tmp_path / "home"
    _run(["kb", "build", "--source", str(source)], home, cwd=tmp_path)
    miss = _run(["kb", "search", "zzzqqq"], home, cwd=tmp_path)
    assert miss.returncode == 1
    assert "jangan dikutip" in miss.stdout


def test_build_missing_source_is_usage_error(tmp_path):
    proc = _run(["kb", "build", "--source", str(tmp_path / "nope")],
                tmp_path / "home", cwd=tmp_path)
    assert proc.returncode != 0
    assert "tidak ada" in (proc.stdout + proc.stderr)