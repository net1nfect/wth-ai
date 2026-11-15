"""Global entry point — pip install wth-ai / PyInstaller binary.

GAP-012 (WU-208 / ARCH-02): implementasi CLI kini tinggal di
`wth_ai/main_cli.py` sehingga ikut wheel. Entry point ini:
- dari source checkout: menjalankan `wth-main.py` di root (perilaku lama);
- dari wheel terinstall: menjalankan `wth_ai/main_cli.py` langsung;
- tidak pernah `chdir` ke site-packages.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _resolve_root() -> Path | None:
    """Root source checkout, atau None bila berjalan dari instalasi wheel."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    candidate = Path(__file__).resolve().parents[2]
    # Jangan salah kira site-packages sebagai source root: hanya checkout yang
    # punya launcher root + package wth_ai di direktori yang sama.
    if (candidate / "wth-main.py").is_file() and (candidate / "wth_ai").is_dir():
        return candidate
    return None


def _resolve_home() -> Path:
    home = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))
    home.mkdir(parents=True, exist_ok=True)
    for sub in ("data/sessions", "data/engagements", "logs", "recon", "wth-results", "tools/bin", "runtime", "config"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    tools_bin = home / "tools" / "bin"
    path = os.environ.get("PATH", "")
    if str(tools_bin) not in path.split(os.pathsep):
        os.environ["PATH"] = f"{tools_bin}{os.pathsep}{path}"
    os.environ.setdefault("GOBIN", str(tools_bin))
    return home


def _bootstrap() -> tuple[Path | None, Path]:
    root = _resolve_root()
    home = _resolve_home()
    os.environ.setdefault("WTH_HOME", str(home))
    if root is not None:
        os.chdir(home if getattr(sys, "frozen", False) else root)
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    # Installed wheel: pertahankan cwd pemanggil; paket importable dari
    # site-packages. Jangan chdir ke site-packages.
    return root, home


def _find_main_script(root: Path | None) -> Path | None:
    """Locate the CLI implementation across layouts.

    Urutan: source/PyInstaller root -> Docker `/app` -> paket terinstall
    (`wth_ai/main_cli.py`) -> cwd -> legacy root. `pip install` sekarang
    menemukan implementasi di dalam paket, bukan mengandalkan file root yang
    tidak ikut wheel (WU-208/ARCH-02).
    """
    candidates: list[Path] = []
    if root is not None:
        candidates.append(root / "wth-main.py")            # source checkout / _MEIPASS
    candidates.extend([
        Path("/app/wth-main.py"),                          # Docker image layout
        Path(__file__).resolve().parents[1] / "main_cli.py",  # wheel terinstall
        Path.cwd() / "wth-main.py",                        # invoked from repo cwd
        Path(__file__).resolve().parents[2] / "wth-main.py",
    ])
    seen: set[Path] = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        if c.is_file():
            return c
    return None


def main() -> None:
    root, home = _bootstrap()
    try:
        from dotenv import load_dotenv
        load_dotenv(home / ".env")
        if root is not None:
            load_dotenv(root / ".env")
    except ImportError:
        pass
    script = _find_main_script(root)
    if script is None:
        raise SystemExit(
            "wth-ai: could not locate the CLI entry point ('wth-main.py' or "
            "packaged 'wth_ai/main_cli.py'). Reinstall the wheel, or run from "
            "a source checkout (python3 wth-main.py ...). WU-208/ARCH-02."
        )
    import runpy
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
