"""P1-0: tidak ada secret di artefak temuan, bundel, atau evidence vault.

AGENTS.md §3(7): secret tidak pernah plaintext di log, laporan, hasil, atau
fixture. Dulu `artifacts_write.write_findings` dan `bundle.write_bundle`
menulis `evidence`/`attestation` mentah, dan `EvidenceVault.store` hanya
meredaksi string top-level.
"""

from __future__ import annotations

import json

from wth_ai.evidence.vault import EvidenceVault
from wth_ai.redteam.artifacts_write import write_findings
from wth_ai.redteam.bundle import write_bundle
from wth_ai.redteam.models import AttemptState, Finding

SECRET = "sk-live-abcdefghijklmnop"
HEADER = f"Authorization: Bearer {SECRET}"


def _finding() -> Finding:
    finding = Finding(id="F-001", hypothesis="BOLA /orders", severity="high")
    finding.transition(AttemptState.ATTEMPTING)
    finding.transition(AttemptState.CONFIRMED)
    finding.evidence = [HEADER]
    finding.attestation = {"note": HEADER}
    return finding


def _assert_clean(root):
    for path in root.rglob("*"):
        if path.is_file():
            assert SECRET not in path.read_text(encoding="utf-8"), path


def test_findings_artifacts_are_redacted(tmp_path):
    write_findings(tmp_path, [_finding()])
    _assert_clean(tmp_path)
    assert "REDACTED" in (tmp_path / "vulnerabilities" / "F-001.md").read_text()


def test_bundle_is_redacted(tmp_path):
    out = tmp_path / "bundle"
    write_bundle(out, findings=[_finding()],
                 scope_card={"target": "10.0.0.1", "authorized": True},
                 audit_events=[{"event": "start", "header": HEADER}])
    _assert_clean(out)


def test_vault_redacts_nested_headers(tmp_path):
    vault = EvidenceVault("sess-p10", base_dir=tmp_path)
    vault.store(finding_id="F-001",
                request={"method": "GET", "headers": {"authorization": f"Bearer {SECRET}"}},
                response={"status": 200, "body": json.dumps({"token": SECRET})})
    _assert_clean(vault.dir)


def test_one_redactor_for_every_surface():
    """Tiga implementasi lokal sekarang menunjuk `wth_ai.redaction`."""
    from wth_ai.rpc.execute_server import redact_secrets as rpc_redact
    from wth_ai.skills.autowrite import mask_secret_assignments
    from wth_ai.redaction import redact_secrets

    raw = f"token={SECRET}"
    assert rpc_redact(raw) == redact_secrets(raw)
    assert mask_secret_assignments(raw) == redact_secrets(raw)