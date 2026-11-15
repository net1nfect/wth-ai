"""Resolusi resource multi-titik: paket vs pohon source (dev).

GAP-012: sebelum ini, data runtime (prompts/, arena/templates/, manifest, rules)
diselesaikan lewat `Path(__file__).resolve().parents[2] / "prompts"` — jalan
kala dicek dari root repo, PECAH setelah `pip install` (parents[2] bukan lagi
root project). Sumber kebenaran sekarang: paket dulu, pohon source sebagai
fallback (dev). Semua pemuat data memakai modul ini.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def ship_dir(name: str) -> Path | None:
    """Direktori data yang 'dikirim' bersama paket (mis. wth_ai/prompts)."""
    pkg = PACKAGE_DIR / name
    return pkg if pkg.exists() else None


def legacy_root_dir(name: str) -> Path | None:
    """Direktori di pohon source (root repo) untuk dev tanpa install."""
    legacy = PACKAGE_DIR.parent / name
    return legacy if legacy.exists() else None


def prompts_dir() -> Path:
    """Lokasi prompts: paket -> source root. Tidak pernah None (fallback final)."""
    pkg = ship_dir("prompts")
    if pkg:
        return pkg
    legacy = legacy_root_dir("prompts")
    return legacy if legacy else (PACKAGE_DIR / "prompts")


def resolve_configured(configured: str | None) -> Path | None:
    """Resolve `config.prompts_dir`:
    - tidak diisi → None (pemuat memakai `prompts_dir()`)
    - absolut → pakai langsung (kalau ada)
    - relatif → coba relatif terhadap paket, lalu relatif cwd.
    """
    if not configured:
        return None
    path = Path(configured)
    if path.is_absolute():
        return path if path.exists() else None
    pkg_cand = PACKAGE_DIR / path
    if pkg_cand.exists():
        return pkg_cand
    return path if path.exists() else None


def data_path(package_relative: str) -> Path:
    """Data non-prompts di dalam paket (manifest, rules, arena/templates)."""
    return PACKAGE_DIR / package_relative
