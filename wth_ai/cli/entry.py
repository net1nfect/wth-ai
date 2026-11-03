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


def _find_main_script(root: Path) -> Path | None:
    """WU-208 (ARCH-02): locate `wth-main.py` across the layouts the console
    script can run in. It is a hyphenated root file (not an importable
    module), so the only way to launch it is by path. Previously `main()`
    hard-coded `root / "wth-main.py"`, which only exists in a source checkout
    -- a `pip install`ed wheel (where it is not packaged) or a differently
    laid-out container gave a cryptic runpy FileNotFoundError. Now we probe
    the real candidate locations and return the first that exists.

    NOTE (honest scope): this fixes source/Docker/cwd launches and turns the
    not-found case into a clear error, but it does NOT by itself make
    `pip install wth-ai` work -- that needs `wth-main.py` packaged into the
    wheel (or the CLI moved into the `wth_ai` package as an importable
    module), a larger change requiring wheel-build verification. Tracked as
    the remaining part of WU-208.
    """
    candidates = [
        root / "wth-main.py",              # source checkout / PyInstaller _MEIPASS
        Path("/app/wth-main.py"),          # Docker image layout (COPY wth-main.py ./)
        Path.cwd() / "wth-main.py",        # invoked from repo cwd
        Path(__file__).resolve().parents[2] / "wth-main.py",
    ]
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
        load_dotenv(root / ".env")
    except ImportError:
        pass
    script = _find_main_script(root)
    if script is None:
        raise SystemExit(
            "wth-ai: could not locate 'wth-main.py' launcher. Run from a source "
            "checkout (python3 wth-main.py ...), or see install.sh. A packaged "
            "pip-install entry point is not yet available (WU-208/ARCH-02)."
        )
    import runpy
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
