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
    "wth-results",
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


def secure_dir(path: Path) -> None:
    """chmod 700 a directory (best-effort)."""
    if path.exists():
        _chmod_safe(path, stat.S_IRWXU)  # 0o700


def secure_file(path: Path) -> None:
    """chmod 600 a file (best-effort)."""
    if path.exists():
        _chmod_safe(path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600


def init_workspace(home: Path | None = None) -> Path:
    """Create the full workspace layout idempotently.

    Existing config/secret files are left untouched; only missing directories
    and the empty sqlite db are created. Permissions are tightened on every run.
    """
    base = home or wth_home()
    base.mkdir(parents=True, exist_ok=True)
    secure_dir(base)

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
