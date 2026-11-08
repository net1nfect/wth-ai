"""kb install: bundle air-gap diverifikasi sha256 sebelum terpasang."""

import hashlib
import json
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


def _make_bundle(bundle_dir: Path, *, corrupt: bool = False) -> Path:
    """Bangun bundle KB minimal (kb.sqlite + manifest.json)."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    db_path = bundle_dir / "kb.sqlite"
    from wth_ai.kb.db import connect, upsert_cve
    conn = connect(db_path)
    upsert_cve(conn, {"cve_id": "CVE-2021-44228", "cvss": 10.0,
                       "assessment_ready": True})
    conn.close()

    digest = hashlib.sha256(db_path.read_bytes()).hexdigest()
    if corrupt:
        digest = "0" * 64
    manifest = {"built_at": "2026-11-05", "total": 1, "ready": 1,
                "sha256": digest}
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    return bundle_dir


def test_install_happy_path(tmp_path):
    home = tmp_path / "home"
    bundle = _make_bundle(tmp_path / "bundle")
    proc = _run(["kb", "install", "--bundle", str(bundle)], home)
    assert proc.returncode == 0, proc.stderr
    assert "terpasang" in proc.stdout
    assert (home / "kb" / "kb.sqlite").is_file()
    assert "1 record · 1 siap-assessment" in proc.stdout


def test_install_rejects_mismatched_sha256(tmp_path):
    home = tmp_path / "home"
    bundle = _make_bundle(tmp_path / "bundle", corrupt=True)
    proc = _run(["kb", "install", "--bundle", str(bundle)], home)
    assert proc.returncode != 0
    assert "kb install:" in proc.stdout + proc.stderr
    assert not (home / "kb" / "kb.sqlite").exists()


def test_install_rejects_missing_manifest(tmp_path):
    home = tmp_path / "home"
    bundle = tmp_path / "bundle"
    bundle.mkdir(exist_ok=True)
    (bundle / "kb.sqlite").write_bytes(b"placeholder")
    proc = _run(["kb", "install", "--bundle", str(bundle)], home)
    assert proc.returncode != 0
    assert "manifest" in proc.stdout + proc.stderr
