"""CLI KB: status kosong, CVE palsu ditolak, CVE ada diterima."""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(args, home):
    env = {**os.environ, "WTH_HOME": str(home), "WTH_SKIP_SETUP": "1",
           "WTH_PLAIN": "1"}
    return subprocess.run([sys.executable, "wth-ai-main.py", *args], cwd=REPO,
                          capture_output=True, text=True, timeout=120, env=env)


def _seed(home):
    from wth_ai.kb.db import connect, set_meta, upsert_cve

    conn = connect(home / "kb" / "kb.sqlite")
    upsert_cve(conn, {"cve_id": "CVE-2021-44228", "cvss": 10.0, "kev": True,
                      "assessment_ready": True, "cwe": "CWE-502",
                      "product": "log4j", "summary": "Log4Shell"})
    set_meta(conn, "built_at", "2026-11-04")
    conn.close()


def test_status_on_empty_kb(tmp_path):
    proc = _run(["kb", "status"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "0 record" in proc.stdout


def test_known_cve_is_printed(tmp_path):
    _seed(tmp_path)
    proc = _run(["kb", "get", "CVE-2021-44228"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "cve_id: CVE-2021-44228" in proc.stdout


def test_unknown_cve_is_refused(tmp_path):
    _seed(tmp_path)
    proc = _run(["kb", "get", "CVE-2026-99999"], tmp_path)
    assert proc.returncode == 2
    assert "TIDAK ADA" in proc.stdout