"""Batasi target ke file yang berubah pada PR (`--scope-mode diff`).

Dipanggil sebelum tool apa pun berjalan: kalau daftar file kosong, tidak ada yang
dipindai. Lebih baik tidak memindai apa pun daripada memindai seluruh repo karena
`git diff` gagal.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlparse

SCOPE_MODE_FULL = "full"
SCOPE_MODE_DIFF = "diff"


class DiffScopeError(RuntimeError):
    """`git diff` gagal — jangan lanjut dengan target tebakan."""


def changed_files(repo: Path, base: str) -> list[str]:
    repo = Path(repo)
    if not base:
        raise DiffScopeError("--diff-base kosong")
    out = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", f"{base}...HEAD"],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise DiffScopeError(f"git diff gagal: {out.stderr.strip()}")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def select(files: list[str], targets: list[str], *,
           suffixes: tuple[str, ...] = ()) -> list[str]:
    """Target yang namanya muncul di daftar file berubah (opsional per suffix).

    Pencocokan hanya dua bentuk, supaya tidak ada false positive:
      1. path persis (setelah dinormalisasi),
      2. target URL yang path-nya ada di daftar file berubah.
    Tidak ada pencocokan substring bebas: "x" tidak boleh cocok dengan
    "fixtures/orders.json" hanya karena huruf x ada di dalamnya.
    """
    changed = {Path(name).as_posix() for name in files}
    allowed = ({name for name in changed if name.endswith(suffixes)}
               if suffixes else changed)
    picked: list[str] = []
    for target in targets:
        candidate = Path(target).as_posix()
        if candidate in allowed:
            picked.append(target)
            continue
        if "://" in target:
            path = urlparse(target).path.lstrip("/")
            if path and any(name.endswith(path) for name in allowed):
                picked.append(target)
    return picked
