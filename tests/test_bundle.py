"""Bundel bukti: manifest, rantai audit, dan deteksi tamper."""

import json

from wth_ai.redteam.bundle import verify_bundle, write_bundle
from wth_ai.redteam.models import Finding


def _bundle(tmp_path):
    out = tmp_path / "bundle"
    write_bundle(out,
                 findings=[Finding(id="F-1", hypothesis="BOLA")],
                 scope_card={"target": "172.28.0.10", "authorized": True},
                 audit_events=[{"event": "start"}, {"event": "finish"}])
    return out


def test_manifest_lists_every_file(tmp_path):
    out = _bundle(tmp_path)
    manifest = json.loads((out / "manifest.json").read_text())
    names = {entry["path"] for entry in manifest["files"]}
    assert {"findings.json", "scope_card.json", "audit.jsonl"} <= names
    assert manifest["root_hash"] == (out / "root_hash.txt").read_text().strip()


def test_audit_chain_links_hashes(tmp_path):
    out = _bundle(tmp_path)
    rows = [json.loads(line) for line in
            (out / "audit.jsonl").read_text().splitlines()]
    assert rows[0]["prev_hash"] == "0" * 64
    assert rows[1]["prev_hash"] == rows[0]["hash"]


def test_clean_bundle_verifies(tmp_path):
    ok, problems = verify_bundle(_bundle(tmp_path))
    assert ok is True and problems == []


def test_tamper_is_detected(tmp_path):
    out = _bundle(tmp_path)
    findings = out / "findings.json"
    findings.write_text(findings.read_text().replace('"F-1"', '"F-2"'))
    ok, problems = verify_bundle(out)
    assert ok is False
    assert any("findings.json" in p for p in problems)


def test_missing_manifest_fails(tmp_path):
    ok, problems = verify_bundle(tmp_path)
    assert ok is False and problems