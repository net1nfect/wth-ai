"""Preflight installer: cek lingkungan, buat workspace, verifikasi runtime.

Kenapa modul ini ada
--------------------
`wth setup` mengonfigurasi provider; ia mengandaikan paket Python dan workspace
sudah siap. Kalau yang hilang adalah dependensi atau direktori, pengguna melihat
traceback `ModuleNotFoundError` di tengah REPL — jauh dari penyebabnya.

Preflight di sini sengaja TIDAK mengubah apa pun kecuali diminta:
`build_report()` hanya membaca, `install_missing()` memasang, `apply_setup()`
menulis workspace + smoke test. Pemisahan itu yang membuatnya bisa diuji dan
dipakai ulang oleh `wth setup --check` tanpa efek samping.

Tidak ada `shell=True` di sini: subprocess memakai daftar argv.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from wth_ai import __version__
from wth_ai.workspace import wth_home

MIN_PYTHON = (3, 10)

#: Distribusi pip untuk setiap modul yang dibutuhkan jalur inti.
#: Nilai = (import name, pip requirement).
CORE_DEPS: tuple[tuple[str, str], ...] = (
    ("click", "click>=8.1"),
    ("rich", "rich>=13.6"),
    ("yaml", "pyyaml>=6.0"),
    ("requests", "requests>=2.31"),
    ("cryptography", "cryptography>=41.0"),
    ("dotenv", "python-dotenv>=1.0"),
    ("prompt_toolkit", "prompt-toolkit>=3.0"),
    ("pydantic", "pydantic>=2.4"),
)

#: Fitur opsional — dilaporkan, tidak pernah memblokir instalasi.
OPTIONAL_DEPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("textual", "textual>=0.80", ("tui",)),
    ("telegram", "python-telegram-bot>=21.0", ("bot",)),
    ("discord", "discord.py>=2.3", ("bot",)),
    ("nodriver", "nodriver>=0.38", ("browser",)),
    ("aiohttp", "aiohttp>=3.9", ("full",)),
    ("bs4", "beautifulsoup4>=4.12", ("full",)),
    ("lxml", "lxml>=5.0", ("full",)),
)

EXTRAS_FOR_FEATURES: dict[str, str] = {
    "tui": "textual>=0.80",
    "bot": "discord.py>=2.3 python-telegram-bot>=21.0",
    "browser": "nodriver>=0.38",
    "full": "aiohttp>=3.9 beautifulsoup4>=4.12 lxml>=5.0",
}


@dataclass(frozen=True)
class DepStatus:
    module: str
    requirement: str
    present: bool
    #: Nilai `--with` yang memasang dependensi ini (kosong = wajib).
    feature: str = ""

    @property
    def required(self) -> bool:
        return not self.feature


@dataclass
class PreflightReport:
    python_ok: bool
    python_version: str
    home: Path
    missing_core: list[DepStatus] = field(default_factory=list)
    missing_optional: list[DepStatus] = field(default_factory=list)
    has_pip: bool = False
    has_git: bool = False
    has_go: bool = False
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.python_ok and not self.missing_core and not self.issues

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "python_ok": self.python_ok,
            "python_version": self.python_version,
            "home": str(self.home),
            "missing_core": [d.requirement for d in self.missing_core],
            "missing_optional": [d.requirement for d in self.missing_optional],
            "has_pip": self.has_pip,
            "has_git": self.has_git,
            "has_go": self.has_go,
            "issues": list(self.issues),
        }


# --------------------------------------------------------------------------- #
# deteksi
# --------------------------------------------------------------------------- #
def module_present(name: str) -> bool:
    """Cek modul tanpa mengimpornya (impor berat bisa punya efek samping)."""
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _check(module: str, requirement: str, feature: str = "") -> DepStatus:
    return DepStatus(module, requirement, module_present(module), feature)


def build_report(*, home: Path | None = None) -> PreflightReport:
    """Baca lingkungan. Tidak menulis apa pun."""
    version = ".".join(str(p) for p in sys.version_info[:3])
    report = PreflightReport(
        python_ok=sys.version_info[:2] >= MIN_PYTHON,
        python_version=version,
        home=home or wth_home(),
        missing_core=[d for d in (_check(m, r) for m, r in CORE_DEPS) if not d.present],
        has_pip=module_present("pip"),
        has_git=shutil.which("git") is not None,
        has_go=shutil.which("go") is not None,
    )
    for module, requirement, features in OPTIONAL_DEPS:
        status = _check(module, requirement, features[0] if features else "")
        if not status.present:
            report.missing_optional.append(status)
    if not report.python_ok:
        report.issues.append(
            f"Python {version} terlalu lama — butuh >= {'.'.join(map(str, MIN_PYTHON))}"
        )
    if sys.platform.startswith("win") and not os.environ.get("WTH_HOME"):
        report.issues.append("Windows: set WTH_HOME eksplisit (mis. %USERPROFILE%\\.wth-ai)")
    return report


def format_report(report: PreflightReport) -> str:
    lines = [
        f"WTH-AI {__version__} preflight",
        f"  python      {report.python_version}{'  ✓' if report.python_ok else '  ✗ butuh >=3.10'}",
        f"  platform    {platform.system()} {platform.machine()}",
        f"  WTH_HOME    {report.home}",
        f"  pip         {'ada' if report.has_pip else 'tidak ada (pip tidak bisa dipakai untuk --install)'}",
        f"  git         {'ada' if report.has_git else 'tidak ada'}",
        f"  go          {'ada' if report.has_go else 'tidak ada (tool Go di ~/.wth-ai/tools/bin tetap bisa dipakai)'}",
        "",
    ]
    if report.missing_core:
        lines.append("Dependensi WAJIB belum terpasang:")
        for dep in report.missing_core:
            lines.append(f"  ✗ {dep.requirement}")
        lines.append("")
    else:
        lines.append("Dependensi wajib: semua terpasang ✓")

    if report.missing_optional:
        lines.append("")
        lines.append("Fitur opsional tidak aktif:")
        for dep in report.missing_optional:
            lines.append(f"  · {dep.requirement}  (--with {dep.feature})")

    if report.issues:
        lines.append("")
        for issue in report.issues:
            lines.append(f"  ! {issue}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# pemasangan
# --------------------------------------------------------------------------- #
def pip_argv(requirements: list[str], *, user: bool = False) -> list[str]:
    """argv pip; `--user` hanya di luar venv (pip menolaknya di dalam venv)."""
    argv = [sys.executable, "-m", "pip", "install"]
    if user and not in_virtualenv():
        argv.append("--user")
    return argv + requirements


def in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def requirements_for(features: list[str], report: PreflightReport) -> list[str]:
    """Daftar requirement untuk `--with tui,bot` + dependensi wajib yang hilang."""
    wanted = [d.requirement for d in report.missing_core]
    for feature in features:
        extra = EXTRAS_FOR_FEATURES.get(feature)
        if not extra:
            continue
        for part in extra.split():
            if part not in wanted:
                wanted.append(part)
    return wanted


def install_missing(
    report: PreflightReport,
    *,
    features: list[str] | None = None,
    dry_run: bool = False,
    runner: object = None,
) -> tuple[int, list[str]]:
    """Pasang dependensi yang kurang. Return (exit code, daftar command).

    Exit 0 = tidak ada yang perlu dipasang / semuanya sukses.
    """
    reqs = requirements_for(features or [], report)
    if not reqs:
        return 0, []
    argv = pip_argv(reqs)
    if dry_run:
        return 0, [" ".join(argv)]
    run = runner or subprocess.run
    proc = run(argv, check=False)  # type: ignore[operator]
    return int(getattr(proc, "returncode", 1)), [" ".join(argv)]


# --------------------------------------------------------------------------- #
# workspace + smoke
# --------------------------------------------------------------------------- #
def apply_setup(*, home: Path | None = None, smoke: bool = True) -> tuple[PreflightReport, str]:
    """Buat workspace, lalu (opsional) smoke test provider.

    Workspace dibuat lewat `init_workspace()` supaya layout dan permission-nya
    persis sama dengan yang dipakai runtime — bukan daftar direktori kedua.
    """
    from wth_ai.workspace import init_workspace

    report = build_report(home=home)
    init_workspace(report.home)

    note = ""
    if smoke:
        note = _smoke_line()
    return report, note


def _smoke_line() -> str:
    """Satu baris hasil smoke test; tidak pernah melempar."""
    try:
        from wth_ai.config import WTHConfig
        from wth_ai.llm.smoke import run_smoke_test

        config = WTHConfig.load()
        role = config.providers.get("planner")
        if role is None:
            return "  smoke: dilewati (belum ada provider — jalankan `wth setup` atau `/connect`)"
        report = run_smoke_test(
            config, role.type, model=role.model, completion_timeout=20,
        )
        ok = bool(report.get("ok")) if isinstance(report, dict) else bool(getattr(report, "ok", False))
        return f"  smoke: {role.type}/{role.model} — {'OK' if ok else 'belum merespons'}"
    except Exception as exc:  # noqa: BLE001 - preflight tidak boleh menggagalkan setup
        return f"  smoke: dilewati ({type(exc).__name__}: {str(exc)[:100]})"