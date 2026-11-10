"""`wth-ai scan --target` di luar scope card harus gagal SEBELUM menjalankan run.

Item 10: kartu scope dulu dimuat setelah `run_offline_demo()` selesai, jadi
target di luar scope tetap memicu seluruh pipeline (dan artefak) sebelum
`enforce_scope` menurunkan status temuan.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

def _cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "wth-ai-main.py", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

@pytest.fixture()
def home(tmp_path, monkeypatch):
    env = {
        **os.environ,
        "WTH_HOME": str(tmp_path / "home"),
        "WTH_SKIP_SETUP": "1",
        "WTH_PLAIN": "1",
    }
    made = _cli(env, "scope", "--target", "10.20.30.40", "--authorized")
    assert made.returncode == 0, made.stdout + made.stderr
    return env

def test_out_of_scope_target_creates_no_artifacts(home, tmp_path):
    """Target di luar kartu: exit non-zero, dan tidak ada direktori out."""
    out = tmp_path / "scan-out"
    res = _cli(home, "scan", "--offline-demo", "--target", "192.0.2.99",
               "--out", str(out))

    assert res.returncode != 0
    assert "di luar scope" in (res.stdout + res.stderr)
    assert not out.exists(), "artefak ditulis walau target di luar scope"

def test_in_scope_target_still_scans(home, tmp_path):
    """Perbaikan tidak memblokir target yang memang in-scope."""
    out = tmp_path / "scan-out"
    res = _cli(home, "scan", "--offline-demo", "--target", "10.20.30.40",
               "--out", str(out))

    assert res.returncode == 0, res.stdout + res.stderr
    assert (out / "bundle" / "manifest.json").is_file()

def test_no_scope_card_still_runs_but_marks_unauthorized(tmp_path):
    """Tanpa kartu: run tetap jalan (demo), bundel jujur authorized=false."""
    env = {
        **os.environ,
        "WTH_HOME": str(tmp_path / "empty-home"),
        "WTH_SKIP_SETUP": "1",
        "WTH_PLAIN": "1",
    }
    out = tmp_path / "scan-out"
    res = _cli(env, "scan", "--offline-demo", "--target", "192.0.2.99",
               "--out", str(out))

    assert res.returncode == 0, res.stdout + res.stderr
    import json

    scope = json.loads((out / "bundle" / "scope_card.json").read_text())
    assert scope["authorized"] is False
