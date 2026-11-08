"""P0-2: bundel bukti tidak boleh mengarang otorisasi.

Sebelum ini `scan_cmd` menulis `{"authorized": true, "target": "fixture"}`
sebagai literal ke `write_bundle`, dan `gate.exit_code` tetap menghitung temuan
`CONFIRMED` dari sana. Artefak yang justru dipakai membuktikan disiplin memuat
klaim izin yang tidak pernah diverifikasi.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from wth_ai.redteam.classify import enforce_scope
from wth_ai.redteam.gate import exit_code
from wth_ai.redteam.models import AttemptState, Finding
from wth_ai.scope_card import ScopeCard
from wth_ai.workspace import wth_home

REPO = Path(__file__).resolve().parents[1]


def _confirmed(severity: str = "high") -> Finding:
    f = Finding(id="F-1", hypothesis="x", severity=severity)
    f.transition(AttemptState.ATTEMPTING)
    f.transition(AttemptState.CONFIRMED)
    return f


def _scan(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "WTH_HOME": str(tmp_path), "WTH_SKIP_SETUP": "1",
           "WTH_PLAIN": "1", "WTH_TEST_MODE": "1"}
    return subprocess.run(
        [sys.executable, "wth-ai-main.py", "scan", "--offline-demo",
         "--out", str(tmp_path / "out"), *extra],
        cwd=REPO, capture_output=True, text=True, timeout=180, env=env,
    )


def test_scan_without_card_writes_honest_bundle(tmp_path):
    proc = _scan(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    card = json.loads((tmp_path / "out" / "bundle" / "scope_card.json").read_text())
    assert card["authorized"] is False
    assert card.get("demo") is True

    findings = json.loads((tmp_path / "out" / "bundle" / "findings.json").read_text())
    assert findings, "demo harus tetap menghasilkan temuan"
    assert all(f["state"] != "confirmed" for f in findings)


def test_scan_with_card_copies_the_card(tmp_path):
    ScopeCard(target="172.28.0.10", cidr=["172.28.0.0/16"],
              authorized=True).write(tmp_path / "scope_card.json")
    proc = _scan(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    card = json.loads((tmp_path / "out" / "bundle" / "scope_card.json").read_text())
    assert card["authorized"] is True
    assert card["target"] == "172.28.0.10"
    assert card["cidr"] == ["172.28.0.0/16"]
    # `demo` tetap jujur: ini memang fixture, meski kartunya nyata.
    assert card["demo"] is True


def test_gate_ignores_confirmed_without_authorization():
    assert exit_code([_confirmed("high")], scope_authorized=False) == 0
    assert exit_code([_confirmed("high")], scope_authorized=True) == 2


def test_enforce_scope_demotes_confirmed_without_card():
    findings = [_confirmed()]
    enforce_scope(findings, scope_authorized=False)
    assert findings[0].state is AttemptState.CANDIDATE

    kept = [_confirmed()]
    enforce_scope(kept, scope_authorized=True)
    assert kept[0].state is AttemptState.CONFIRMED


def test_scan_failure_is_not_hidden(tmp_path):
    """Tanpa kartu, scan tetap jalan tetapi tidak boleh mengklaim CONFIRMED."""
    proc = _scan(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (wth_home() / "scope_card.json").exists()


def test_finding_from_dict_cannot_claim_confirmed_without_attestation():
    """`state: confirmed` di JSON adalah klaim, bukan bukti."""
    from wth_ai.redteam.models import Finding

    f = Finding.from_dict({"id": "F-x", "hypothesis": "x", "state": "confirmed"})
    assert f.state is AttemptState.CANDIDATE

    backed = Finding.from_dict({
        "id": "F-y", "hypothesis": "y", "state": "confirmed",
        "attestation": {"level": "confirmed"},
    })
    assert backed.state is AttemptState.CONFIRMED
