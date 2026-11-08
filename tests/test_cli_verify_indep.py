"""CLI: `scan --verify` menjalankan verifikasi independen + attestasi (item 4)."""

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
    return subprocess.run([sys.executable, "wth-ai-main.py", *args], cwd=REPO,
                          capture_output=True, text=True, timeout=180, env=env)


def _read_findings(out):
    rows = json.loads((out / "bundle" / "findings.json").read_text())
    return {r["id"]: r for r in rows}


def test_scan_verify_records_independent_attestation(tmp_path):
    out = tmp_path / "out"
    # Run tanpa --verify: tidak ada attestation independen.
    _run(["scan", "--offline-demo", "--out", str(out)], tmp_path)
    before = _read_findings(out)
    assert all(f.get("attestation", {}).get("verifier_role") != "independent_verifier"
               for f in before.values())

    # Run dengan --verify: temuan ber-evidence-lengkap mendapat attestasi.
    out2 = tmp_path / "out2"
    proc = _run(["scan", "--offline-demo", "--verify", "--out", str(out2)],
                tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "verify: independen" in proc.stdout
    rows = _read_findings(out2)
    attested = [r for r in rows.values()
                if r.get("attestation", {}).get("verifier_role") == "independent_verifier"]
    assert attested, "tidak ada findings yang di-attestasi"
    for r in attested:
        assert r["attestation"]["level"] == "confirmed"
        assert r["attestation"]["isolated_from_discovery"] is True
