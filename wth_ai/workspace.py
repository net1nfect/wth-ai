"""Workspace initialization for ~/.wth-ai/.

Creates the full local workspace layout in an idempotent way and applies
restrictive permissions. Never overwrites existing config/secret files.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


def wth_home() -> Path:
    """Return the active WTH_HOME (re-read from env so tests can override)."""
    return Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))


# Directory layout created on first run (relative to WTH_HOME).
#: Nama direktori hasil pemindaian. Satu konstanta supaya `JobStore`,
#: `ReconEngine`, doctor, dan config default tidak lagi menulis literal sendiri.
RESULTS_DIR_NAME = "wth-ai-results"

WORKSPACE_DIRS: tuple[str, ...] = (
    "config",
    "logs",
    "logs/jobs",
    "reports",
    "screenshots",
    "cache",
    "profiles",
    "data/sessions",
    "data/engagements",
    "tools/bin",
    "runtime",
    "runtime/queue",
    "recon",
    RESULTS_DIR_NAME,
)

# Files that must never be world/group readable.
SENSITIVE_FILES: tuple[str, ...] = (
    "vault.enc",
    "vault.key",
    ".env",
    "config/user.yaml",
)


def _chmod_safe(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        # Best-effort: some filesystems (e.g. mounted volumes) reject chmod.
        pass


def atomic_write_text(path: Path, text: str) -> Path:
    """P1-4: tulis lewat `<path>.tmp` + fsync + `os.replace`.

    Menulis langsung ke path tujuan berarti proses yang mati di tengah tulis
    meninggalkan JSON/YAML terpotong -- dan file lama sudah terhapus lebih dulu.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def secure_dir(path: Path) -> None:
    """chmod 700 a directory (best-effort)."""
    if path.exists():
        _chmod_safe(path, stat.S_IRWXU)  # 0o700


def secure_file(path: Path) -> None:
    """chmod 600 a file (best-effort)."""
    if path.exists():
        _chmod_safe(path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600


def _migrate_legacy_names(base: Path) -> None:
    """Rename sekali dari nama lama ke nama baru; tidak menimpa apa pun.

    Dijalankan SEBELUM direktori baru dibuat, kalau tidak `RESULTS_DIR_NAME`
    sudah ada dan rename tidak pernah terjadi. Aman diulang: syaratnya
    `src.exists() and not dst.exists()`.
    """
    for old, new in (
        ("wth-results", RESULTS_DIR_NAME),
        ("config/wth_config.yaml", "config/wth_ai_config.yaml"),
    ):
        src, dst = base / old, base / new
        if src.exists() and not dst.exists():
            src.rename(dst)


def init_workspace(home: Path | None = None) -> Path:
    """Create the full workspace layout idempotently.

    Existing config/secret files are left untouched; only missing directories
    and the empty sqlite db are created. Permissions are tightened on every run.
    """
    base = home or wth_home()
    base.mkdir(parents=True, exist_ok=True)
    secure_dir(base)

    _migrate_legacy_names(base)

    for sub in WORKSPACE_DIRS:
        d = base / sub
        d.mkdir(parents=True, exist_ok=True)

    # Local sqlite db placeholder (do not clobber an existing one).
    db = base / "db.sqlite"
    if not db.exists():
        db.touch()
    secure_file(db)

    # Tighten permissions on any already-present sensitive files.
    for rel in SENSITIVE_FILES:
        secure_file(base / rel)

    return base


def workspace_status(home: Path | None = None) -> dict[str, object]:
    """Report which workspace pieces exist (used by doctor)."""
    base = home or wth_home()
    missing_dirs = [sub for sub in WORKSPACE_DIRS if not (base / sub).is_dir()]
    return {
        "home": str(base),
        "exists": base.exists(),
        "missing_dirs": missing_dirs,
        "db_exists": (base / "db.sqlite").exists(),
        "complete": base.exists() and not missing_dirs,
    }
