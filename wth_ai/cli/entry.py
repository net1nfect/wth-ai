"""Global entry point — pip install wth-ai / PyInstaller binary."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _resolve_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


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


def _bootstrap() -> tuple[Path, Path]:
    root = _resolve_root()
    home = _resolve_home()
    os.environ.setdefault("WTH_HOME", str(home))
    os.chdir(home if getattr(sys, "frozen", False) else root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root, home


def main() -> None:
    root, home = _bootstrap()
    entry = root / "wth-main.py"
    if not entry.is_file():
        # Instalasi non-editable / wheel tanpa data repo. Pesannya harus
        # menunjuk ke perbaikannya, bukan traceback runpy yang tidak informatif.
        raise SystemExit(
            f"WTH-AI tidak menemukan wth-main.py di {root}.\n"
            "Pasang dari sumber dengan installer resmi:\n"
            "  scripts/install.sh\n"
            "atau: pip install -e <repo>"
        )
    try:
        from dotenv import load_dotenv
        load_dotenv(home / ".env")
        load_dotenv(root / ".env")
    except ImportError:
        pass
    import runpy
    runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    main()
