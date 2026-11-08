"""Verifikasi tanda tangan artefak update (§1.2 poin 4).

Aturannya satu arah: verifikasi gagal berarti **tolak**. Tidak ada fallback ke
"pasang saja tanpa tanda tangan", karena artefak yang tanda tangannya rusak
justru kasus yang paling perlu ditolak. Kunci publik dipin di repo; host unduh
dibatasi allowlist dan tetap lewat `EgressGuard` supaya tidak ada jalur kedua
untuk keluar jaringan.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from wth_ai.audit import audit_log

#: Host artefak resmi. Nama lain harus ditambahkan lewat perubahan kode yang
#: direview, bukan lewat konfigurasi yang bisa diubah saat runtime.
ALLOWED_HOSTS: tuple[str, ...] = ("github.com", "objects.githubusercontent.com")

class UpdateDenied(RuntimeError):
    """Update ditolak: tanda tangan tidak valid atau host tidak diizinkan."""

def host_allowed(url: str) -> bool:
    """True hanya untuk https ke host di `ALLOWED_HOSTS`."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    return (parsed.hostname or "") in ALLOWED_HOSTS

def _public_key(path: Path) -> Ed25519PublicKey | None:
    try:
        raw = path.read_bytes()
        return Ed25519PublicKey.from_public_bytes(raw)
    except (OSError, ValueError):
        return None

def verify_artifact(artifact: Path, signature_path: Path, pubkey_path: Path) -> bool:
    """True hanya bila `signature_path` adalah tanda tangan sah atas isi artefak.

    Semua kegagalan (file hilang, kunci rusak, signature bukan 64 byte, isi
    berubah) menghasilkan False — pemanggil tidak perlu membedakan sebabnya
    untuk memutuskan menolak.
    """
    key = _public_key(Path(pubkey_path))
    if key is None:
        return False
    try:
        payload = Path(artifact).read_bytes()
        signature = Path(signature_path).read_bytes()
    except OSError:
        return False
    try:
        key.verify(signature, payload)
    except InvalidSignature:
        return False
    return True

def install_artifact(artifact: Path, signature_path: Path, pubkey_path: Path,
                     target: Path) -> Path:
    """Salin artefak ke `target` hanya setelah tanda tangannya lolos."""
    if not verify_artifact(artifact, signature_path, pubkey_path):
        audit_log("update_rejected", artifact=str(artifact))
        raise UpdateDenied(
            f"tanda tangan tidak valid untuk {Path(artifact).name}; update ditolak"
        )
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact, target)
    audit_log("update_install", artifact=target.name, size=target.stat().st_size)
    return target
