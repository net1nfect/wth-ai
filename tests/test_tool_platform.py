"""Cross-platform tool installer tests."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.tools.installer import ToolInstaller
from wth_ai.tools.platform import (
    augment_install_spec,
    compatible_methods,
    detect_os,
    extract_apt_package,
    install_order,
    is_windows,
    normalize_install_command,
    should_use_sudo,
)
from wth_ai.tools.registry import ToolRegistry, ToolSpec


def test_extract_apt_package():
    assert extract_apt_package("apt-get install -y subfinder") == "subfinder"


def test_install_order_windows():
    with patch("wth_ai.tools.platform.sys.platform", "win32"):
        order = install_order()
        assert order[0] == "go"
        assert "winget" in order
        assert "apt" not in order


def test_install_order_linux_debian():
    with patch("wth_ai.tools.platform.detect_os") as mock_os:
        mock_os.return_value = MagicMock(system="linux", distro="debian", machine="x86_64")
        order = install_order()
        assert "apt" in order
        assert "winget" not in order


def test_install_order_macos():
    with patch("wth_ai.tools.platform.detect_os") as mock_os:
        mock_os.return_value = MagicMock(system="darwin", distro="macos", machine="arm64")
        order = install_order()
        assert "brew" in order
        assert "apt" not in order


def test_augment_windows_from_apt_and_brew():
    with patch("wth_ai.tools.platform.detect_os") as mock_os:
        mock_os.return_value = MagicMock(system="windows", distro="windows", machine="x86_64")
        spec = augment_install_spec(
            "subfinder",
            {"apt": "apt-get install -y subfinder", "brew": "brew install subfinder", "go": "go install x"},
        )
        assert "choco" in spec
        assert "scoop" in spec
        assert "go" in spec


def test_augment_linux_rhel_dnf():
    with patch("wth_ai.tools.platform.detect_os") as mock_os:
        mock_os.return_value = MagicMock(system="linux", distro="rhel", machine="x86_64")
        spec = augment_install_spec("nmap", {"apt": "apt-get install -y nmap"})
        assert "dnf" in spec


def test_should_not_sudo_on_windows():
    with patch("wth_ai.tools.platform.is_windows", return_value=True):
        assert should_use_sudo("apt", use_sudo=True) is False


def test_pip_normalize_uses_python_m():
    argv, display, shell = normalize_install_command("pip", "pip install dnsgen", use_sudo=False)
    assert shell is False
    assert argv is not None
    assert "pip" in argv[0] or argv[0].endswith("python") or "python" in argv[0]
    assert "install" in argv


def test_normalize_install_is_argv_for_package_managers():
    """P2-9: perintah manager paket dijalankan sebagai argv, bukan shell."""
    argv, display, shell = normalize_install_command("apt", "apt-get install -y nmap", use_sudo=False)
    assert shell is False
    assert argv == ["apt-get", "install", "-y", "nmap"]
    assert display == "apt-get install -y nmap"


def test_normalize_install_keeps_sudo_as_argv_prefix():
    with patch("wth_ai.tools.platform.should_use_sudo", return_value=True):
        argv, _display, shell = normalize_install_command("apt", "apt-get install -y nmap", use_sudo=True)
    assert shell is False
    assert argv[:2] == ["sudo", "apt-get"]


def test_scoop_remains_the_only_shell_exception():
    """`scoop` di Windows adalah skrip PowerShell; argv tidak bisa menjalankannya."""
    argv, _display, shell = normalize_install_command("scoop", "scoop install subfinder", use_sudo=False)
    assert (argv, shell) == (None, True)


def test_no_shell_call_site_outside_the_allowlist():
    from wth_ai.tools.doctor import unallowlisted_shell_sites

    assert unallowlisted_shell_sites() == []


def test_doctor_flags_a_new_shell_call_site(tmp_path):
    from wth_ai.tools.doctor import unallowlisted_shell_sites

    bad = tmp_path / "tools" / "new.py"
    bad.parent.mkdir()
    bad.write_text(
        "import subprocess\n\n\ndef go(cmd):\n    return subprocess.run(cmd, shell=True)\n",
        encoding="utf-8",
    )
    assert unallowlisted_shell_sites(tmp_path) == ["tools/new.py:go"]


def test_pick_installer_prefers_go_on_all_platforms():
    reg = ToolRegistry()
    inst = ToolInstaller(reg)
    spec = ToolSpec(
        id="test-tool",
        binary="testtool",
        category="recon.test",
        install={
            "go": "go install example.com/tool@latest",
            "apt": "apt-get install -y testtool",
        },
    )
    reg.tools["test-tool"] = spec
    with patch("wth_ai.tools.platform.manager_available") as mock_mgr:
        mock_mgr.side_effect = lambda m: m in {"go", "apt"}
        picked = inst._pick_installer(spec)
    assert picked is not None
    assert picked[0] == "go"


def test_install_one_no_installer_reports_platform():
    reg = ToolRegistry()
    inst = ToolInstaller(reg)
    spec = ToolSpec(id="manual-only", binary="missingbin", category="x", install={})
    reg.tools["manual-only"] = spec
    with patch.object(reg, "is_installed", return_value=False):
        result = inst.install_one("manual-only")
    assert result["status"] == "no_installer"
    assert "platform" in result


def test_install_all_skips_incompatible():
    reg = ToolRegistry()
    inst = ToolInstaller(reg)
    spec = ToolSpec(id="apt-only", binary="aptonly", category="x", install={"apt": "apt-get install -y aptonly"})
    reg.tools["apt-only"] = spec
    with patch.object(reg, "missing_tools", return_value=["apt-only"]):
        with patch.object(reg, "is_installed", return_value=False):
            with patch("wth_ai.tools.platform.is_windows", return_value=True):
                with patch("wth_ai.tools.platform.detect_os") as mock_os:
                    mock_os.return_value = MagicMock(system="windows", distro="windows", machine="x86_64")
                    inst.os_info = mock_os.return_value
                    results = inst.install_all_missing(limit=5)
    assert results[0]["status"] == "skipped"


def test_compatible_methods_respects_manager():
    install = {"go": "go install x", "apt": "apt-get install -y x"}
    with patch("wth_ai.tools.platform.manager_available") as mock_mgr:
        mock_mgr.side_effect = lambda m: m == "go"
        methods = compatible_methods(install, "x")
    assert methods == ["go"]
