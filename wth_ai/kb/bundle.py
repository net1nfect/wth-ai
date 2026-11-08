"""Bundle KB air-gap: distribusi KB yang diverifikasi sha256.

Sebuah *bundle* adalah direktori yang berisi:
  kb.sqlite    basis data CVE (hasil `kb build`)
  manifest.json {".built_at": ISO-8601, "total": int, "ready": int,
                  "sha256": "<hex dari kb.sqlite>"}

`install` menyalin kb.sqlite ke workspace setelah mencocokkan sha256, supaya
operator bisa membawa KB lintas udara (air-gap) tanpa mengandalkan jaringan.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

MANIFEST_NAME = "manifest.json"
DB_NAME = "kb.sqlite"


class BundleError(ValueError):
    """Bundle rusak / tidak valid."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(bundle: Path) -> dict:
    manifest_path = bundle / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BundleError(f"bundle tidak punya {MANIFEST_NAME}: {bundle}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BundleError(f"manifest rusak: {manifest_path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("sha256"), str):
        raise BundleError("manifest harus berisi sha256 (string)")
    return data


def verify_bundle(bundle: Path) -> dict:
    """Periksa bahwa bundle valid; kembalikan manifest kalau ya."""
    manifest = load_manifest(bundle)
    db_path = bundle / DB_NAME
    if not db_path.is_file():
        raise BundleError(f"bundle tidak punya {DB_NAME}: {bundle}")
    actual = _sha256(db_path)
    expected = manifest["sha256"].strip().lower()
    if actual != expected:
        raise BundleError(
            f"sha256 {DB_NAME} tidak cocok: {actual[:12]}… != {expected[:12]}…"
        )
    return manifest


def install_bundle(bundle: Path, dest_db: Path) -> dict:
    """Salin kb.sqlite yang tervalidasi ke dest_db; kembalikan manifest."""
    manifest = verify_bundle(bundle)
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bundle / DB_NAME, dest_db)
    return manifest
