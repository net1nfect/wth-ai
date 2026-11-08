"""Cross-platform detection for tool installation."""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class OsInfo:
    system: str  # linux | darwin | windows | other
    distro: str  # debian | rhel | arch | alpine | macos | windows | unknown
    machine: str


def detect_os() -> OsInfo:
    sys_name = sys.platform
    if sys_name.startswith("win"):
        return OsInfo(system="windows", distro="windows", machine=platform.machine())
    if sys_name == "darwin":
        return OsInfo(system="darwin", distro="macos", machine=platform.machine())
    if sys_name.startswith("linux"):
        distro = _linux_distro()
        return OsInfo(system="linux", distro=distro, machine=platform.machine())
    return OsInfo(system="other", distro="unknown", machine=platform.machine())


def _linux_distro() -> str:
    try:
        data = platform.freedesktop_os_release()
        ident = (data.get("ID") or "").lower()
        like = (data.get("ID_LIKE") or "").lower()
        if ident in {"arch", "manjaro"} or "arch" in like:
            return "arch"
        if ident in {"rhel", "fedora", "centos", "rocky", "almalinux"} or "rhel" in like or "fedora" in like:
            return "rhel"
        if ident in {"alpine"}:
            return "alpine"
        if ident in {"debian", "ubuntu", "linuxmint", "pop"} or "debian" in like or "ubuntu" in like:
            return "debian"
    except Exception:
        pass
    if shutil.which("apt-get"):
        return "debian"
    if shutil.which("dnf") or shutil.which("yum"):
        return "rhel"
    if shutil.which("pacman"):
        return "arch"
    return "unknown"


def is_windows() -> bool:
    return detect_os().system == "windows"


def is_macos() -> bool:
    return detect_os().system == "darwin"


def is_linux() -> bool:
    return detect_os().system == "linux"


def pip_command() -> list[str] | None:
    if shutil.which("pip3"):
        return ["pip3"]
    if shutil.which("pip"):
        return ["pip"]
    return [sys.executable, "-m", "pip"]


def manager_available(method: str) -> bool:
    checks = {
        "go": lambda: shutil.which("go"),
        "pip": lambda: pip_command(),
        "npm": lambda: shutil.which("npm"),
        "gem": lambda: shutil.which("gem"),
        "cargo": lambda: shutil.which("cargo"),
        "apt": lambda: is_linux() and shutil.which("apt-get"),
        "dnf": lambda: is_linux() and shutil.which("dnf"),
        "yum": lambda: is_linux() and shutil.which("yum"),
        "pacman": lambda: is_linux() and shutil.which("pacman"),
        "snap": lambda: is_linux() and shutil.which("snap"),
        "brew": lambda: shutil.which("brew"),
        "winget": lambda: is_windows() and shutil.which("winget"),
        "choco": lambda: is_windows() and shutil.which("choco"),
        "scoop": lambda: is_windows() and shutil.which("scoop"),
        "script": lambda: True,
    }
    fn = checks.get(method)
    return bool(fn and fn())


def install_order() -> tuple[str, ...]:
    info = detect_os()
    if info.system == "windows":
        return ("go", "pip", "npm", "gem", "cargo", "winget", "choco", "scoop", "script")
    if info.system == "darwin":
        return ("go", "pip", "npm", "gem", "cargo", "brew", "script")
    # linux — prefer distro package manager when declared
    order: list[str] = ["go", "pip", "npm", "gem", "cargo"]
    if info.distro == "debian":
        order.extend(["apt", "snap", "brew"])
    elif info.distro == "rhel":
        order.extend(["dnf", "yum", "snap", "brew"])
    elif info.distro == "arch":
        order.extend(["pacman", "brew"])
    else:
        order.extend(["apt", "dnf", "yum", "pacman", "snap", "brew"])
    order.append("script")
    return tuple(order)


def should_use_sudo(method: str, *, use_sudo: bool) -> bool:
    if not use_sudo or is_windows() or is_macos():
        return False
    if method not in {"apt", "dnf", "yum", "pacman", "snap"}:
        return False
    try:
        return os.geteuid() != 0
    except AttributeError:
        return False


_APT_PKG_RE = re.compile(r"apt-get\s+install\s+(?:-y\s+)?(\S+)")


def extract_apt_package(apt_cmd: str) -> str | None:
    m = _APT_PKG_RE.search(apt_cmd)
    return m.group(1) if m else None


# Known winget package IDs for common recon tools (optional fallback).
WINGET_BY_TOOL: dict[str, str] = {
    "nmap": "Insecure.Nmap",
    "wireshark": "WiresharkFoundation.Wireshark",
    "curl": "cURL.cURL",
    "git": "Git.Git",
    "python3": "Python.Python.3.12",
    "nodejs": "OpenJS.NodeJS.LTS",
    "go": "GoLang.Go",
    "ruby": "RubyInstallerTeam.Ruby.3.2",
}


def augment_install_spec(tool_id: str, install: dict[str, str]) -> dict[str, str]:
    """Add synthesized install methods for the current OS when manifest lacks them."""
    out = dict(install)
    info = detect_os()

    if info.system == "windows":
        if "winget" not in out and tool_id in WINGET_BY_TOOL:
            wid = WINGET_BY_TOOL[tool_id]
            out["winget"] = (
                f"winget install -e --id {wid} "
                "--accept-package-agreements --accept-source-agreements"
            )
        apt_pkg = extract_apt_package(out.get("apt", ""))
        if apt_pkg and "choco" not in out:
            out["choco"] = f"choco install -y {apt_pkg}"
        brew_cmd = out.get("brew", "")
        if brew_cmd.startswith("brew install ") and "scoop" not in out:
            pkg = brew_cmd.replace("brew install ", "", 1).strip()
            out["scoop"] = f"scoop install {pkg}"

    if info.system == "linux":
        apt_pkg = extract_apt_package(out.get("apt", ""))
        if apt_pkg:
            if info.distro == "rhel" and "dnf" not in out and "yum" not in out:
                out["dnf"] = f"dnf install -y {apt_pkg}"
            if info.distro == "arch" and "pacman" not in out:
                out["pacman"] = f"pacman -S --noconfirm {apt_pkg}"

    return out


def normalize_install_command(method: str, cmd: str, *, use_sudo: bool) -> tuple[list[str] | None, str, bool]:
    """Return (argv, display_cmd, use_shell).

    argv=None berarti jalankan display_cmd dengan shell=True. Hanya `scoop`
    yang butuh itu: di Windows `scoop` adalah skrip PowerShell, bukan binary.
    Perintah lain dijalankan sebagai argv supaya isi manifest tidak pernah
    ditafsirkan oleh shell (P2-9).
    """
    cmd = cmd.strip()
    if should_use_sudo(method, use_sudo=use_sudo):
        cmd = f"sudo {cmd}"

    if method == "pip" or cmd.startswith("pip install") or cmd.startswith("pip3 install"):
        parts = cmd.split()
        if parts[0] in {"pip", "pip3"}:
            base = pip_command() or [sys.executable, "-m", "pip"]
            return base + parts[1:], " ".join(base + parts[1:]), False
        if cmd.startswith("pip install"):
            base = pip_command() or [sys.executable, "-m", "pip"]
            return base + ["install"] + parts[2:], cmd, False

    if method == "scoop":
        return None, cmd, True

    # Pakai `cmd` yang sudah berawalan `sudo`, bukan menambah prefiks kedua.
    return cmd.split(), cmd, False


def platform_summary() -> str:
    info = detect_os()
    managers = [m for m in install_order() if manager_available(m)]
    mgrs = ", ".join(managers[:6]) if managers else "none"
    return f"{info.system}/{info.distro} · managers: {mgrs}"


def compatible_methods(install: dict[str, str], tool_id: str = "") -> list[str]:
    spec = augment_install_spec(tool_id, install)
    order = install_order()
    return [m for m in order if m in spec and manager_available(m)]
