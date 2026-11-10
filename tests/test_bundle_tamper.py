"""verify_bundle: manifest cacat tidak boleh crash, dan file selundupan terdeteksi.

Item 11: `entry["sha256"]` -> KeyError (traceback) bila manifest cacat.
Item 12: hanya entri manifest yang diperiksa, jadi file tambahan lolos.
"""

from __future__ import annotations

import json

from wth_ai.redteam.bundle import verify_bundle, write_bundle
from wth_ai.redteam.models import Finding

def _bundle(tmp_path):
    out = tmp_path / "bundle"
    write_bundle(
        out,
        findings=[Finding(id="F-1", hypothesis="BOLA")],
        scope_card={"target": "172.28.0.10", "authorized": True},
        audit_events=[{"event": "start"}],
    )
    return out

def test_manifest_entry_without_sha256_reports_problem(tmp_path):
    """Entri tanpa sha256 -> masalah terdaftar, bukan KeyError."""
    out = _bundle(tmp_path)
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["files"][0].pop("sha256", None)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))

    ok, problems = verify_bundle(out)

    assert ok is False
    assert any("sha256" in p for p in problems), problems

def test_unlisted_file_is_detected(tmp_path):
    """File yang tidak tercatat di manifest membuat bundel tidak valid."""
    out = _bundle(tmp_path)
    (out / "injected.json").write_text('{"injected": true}')

    ok, problems = verify_bundle(out)

    assert ok is False
    assert any("injected.json" in p for p in problems), problems

def test_clean_bundle_still_verifies(tmp_path):
    """Perbaikan tidak menandai file sah: root_hash.txt memang tercatat."""
    ok, problems = verify_bundle(_bundle(tmp_path))
    assert ok is True and problems == []
