"""Updater: tanda tangan wajib lolos, allowlist host ditegakkan (§1.2 poin 4).

Gagal verifikasi berarti **tolak**, bukan jatuh ke jalur tanpa tanda tangan.
Test ini menandatangani fixture dengan Ed25519 sungguhan; tidak ada kunci palsu
yang bisa membuat verifikasi "lulus" secara kebetulan.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from wth_ai.update.verifier import (
    ALLOWED_HOSTS,
    UpdateDenied,
    host_allowed,
    install_artifact,
    verify_artifact,
)


def _keys(tmp_path: Path) -> tuple[Path, Path, Ed25519PrivateKey]:
    key = Ed25519PrivateKey.generate()
    pub = tmp_path / "update.pub"
    pub.write_bytes(key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ))
    artifact = tmp_path / "wth-ai.tar.gz"
    artifact.write_bytes(b"artifact-bytes")
    sig = tmp_path / "wth-ai.tar.gz.sig"
    sig.write_bytes(key.sign(artifact.read_bytes()))
    return artifact, sig, key

def _sign(path: Path, key: Ed25519PrivateKey) -> None:
    path.with_suffix(path.suffix + ".sig").write_bytes(key.sign(path.read_bytes()))

def test_valid_signature_is_accepted(tmp_path: Path) -> None:
    artifact, sig, _ = _keys(tmp_path)
    assert verify_artifact(artifact, sig, tmp_path / "update.pub") is True

def test_corrupted_signature_is_rejected(tmp_path: Path) -> None:
    artifact, sig, _ = _keys(tmp_path)
    raw = bytearray(sig.read_bytes())
    raw[0] ^= 0xFF
    sig.write_bytes(bytes(raw))
    assert verify_artifact(artifact, sig, tmp_path / "update.pub") is False

def test_tampered_artifact_is_rejected(tmp_path: Path) -> None:
    artifact, sig, _ = _keys(tmp_path)
    artifact.write_bytes(b"artifact-bytes-evil")
    assert verify_artifact(artifact, sig, tmp_path / "update.pub") is False

def test_wrong_key_is_rejected(tmp_path: Path) -> None:
    artifact, sig, _ = _keys(tmp_path)
    other = Ed25519PrivateKey.generate()
    other_pub = tmp_path / "other.pub"
    other_pub.write_bytes(other.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ))
    assert verify_artifact(artifact, sig, other_pub) is False

def test_missing_files_are_rejected_not_raised(tmp_path: Path) -> None:
    artifact, sig, _ = _keys(tmp_path)
    assert verify_artifact(tmp_path / "nope", sig, tmp_path / "update.pub") is False
    assert verify_artifact(artifact, tmp_path / "nope", tmp_path / "update.pub") is False
    assert verify_artifact(artifact, sig, tmp_path / "nope") is False

def test_malformed_signature_length_is_rejected(tmp_path: Path) -> None:
    artifact, sig, _ = _keys(tmp_path)
    sig.write_bytes(b"pendek")
    assert verify_artifact(artifact, sig, tmp_path / "update.pub") is False

def test_host_allowlist() -> None:
    assert "github.com" in ALLOWED_HOSTS
    assert host_allowed("https://github.com/org/repo/releases/download/v1/a.tgz") is True
    assert host_allowed("https://evil.example.com/a.tgz") is False
    assert host_allowed("file:///etc/passwd") is False

def test_install_refuses_unverified_artifact(tmp_path: Path, monkeypatch) -> None:
    artifact, sig, _ = _keys(tmp_path)
    sig.write_bytes(b"x" * 64)
    target = tmp_path / "install" / "wth-ai.tar.gz"

    with pytest.raises(UpdateDenied, match="tanda tangan"):
        install_artifact(artifact, sig, tmp_path / "update.pub", target)
    assert not target.exists()

def test_install_copies_verified_artifact(tmp_path: Path) -> None:
    artifact, sig, _ = _keys(tmp_path)
    target = tmp_path / "install" / "wth-ai.tar.gz"

    install_artifact(artifact, sig, tmp_path / "update.pub", target)

    assert target.read_bytes() == artifact.read_bytes()

def test_install_is_audited(tmp_path: Path, monkeypatch) -> None:
    artifact, sig, _ = _keys(tmp_path)
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    install_artifact(artifact, sig, tmp_path / "update.pub", tmp_path / "out.tar.gz")

    audit = (tmp_path / "home" / "logs" / "audit.log").read_text(encoding="utf-8")
    assert "update_install" in audit
    assert json.loads(audit.strip().splitlines()[-1])["event"] == "update_install"
