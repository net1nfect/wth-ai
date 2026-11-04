"""Tangkap screenshot lewat Playwright CLI, bila tersedia.

Playwright dipanggil sebagai binary eksternal (bukan library), mengikuti pola
terverifikasi tool eksternal di repo ini: kalau tidak terpasang, hasilnya
"unavailable" dengan alasan, bukan crash atau import error.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

DEFAULT_TIMEOUT = 45
WAIT_MS = 1500


def binary() -> str | None:
    """Path binary Playwright, atau None bila tidak terpasang."""
    return shutil.which(os.environ.get("WTH_PLAYWRIGHT_BIN", "playwright"))


def available() -> dict:
    path = binary()
    return {"available": bool(path), "path": path or "",
            "hint": "" if path else "pip install playwright && playwright install chromium"}


def capture(url: str, out_dir: Path, *, name: str = "", timeout: int = DEFAULT_TIMEOUT,
            full_page: bool = True) -> dict:
    """Ambil satu screenshot. Tidak pernah melempar; kegagalan ada di dict."""
    exe = binary()
    if not exe:
        return {"ok": False, "path": "", "url": url,
                "error": available()["hint"]}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = name or (url.replace("://", "_").replace("/", "_")[:80] or "page")
    target = out_dir / f"{safe}.png"
    argv = [exe, "screenshot", f"--wait-for-timeout={WAIT_MS}",
            "--viewport-size=1280,800"]
    if full_page:
        argv.append("--full-page")
    argv += [url, str(target)]
    started = time.time()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "path": str(target), "url": url, "error": str(exc)}
    elapsed = int((time.time() - started) * 1000)
    if proc.returncode != 0 or not target.is_file():
        return {"ok": False, "path": str(target), "url": url,
                "error": (proc.stderr or proc.stdout or "screenshot gagal").strip()[:400],
                "ms": elapsed}
    return {"ok": True, "path": str(target), "url": url, "error": "",
            "ms": elapsed, "bytes": target.stat().st_size}