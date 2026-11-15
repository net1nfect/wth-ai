"""Regression GAP-012 / WU-208 (ARCH-02): packaging `pip install`.

Bukti yang dikunci:
- prompts ikut paket dan tidak bergantung cwd;
- salinan prompts paket tidak drift dari sumber root;
- TUI (textual, optional) tidak diimpor saat `import wth_ai.cli.tui`;
- entry point menemukan `wth_ai/main_cli.py` di layout wheel.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wth_ai import resources
from wth_ai.agents import load_prompt
from wth_ai.config import WTHConfig

_SOURCE_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", sorted(p.name for p in (_SOURCE_ROOT / "prompts").glob("*.txt")))
def test_package_prompts_ship_and_stay_in_sync(name: str) -> None:
    packaged = resources.PACKAGE_DIR / "prompts" / name
    source = _SOURCE_ROOT / "prompts" / name
    assert packaged.is_file(), f"{name} tidak ikut paket (wheel akan kehilangan prompt)"
    assert packaged.read_text(encoding="utf-8") == source.read_text(encoding="utf-8"), (
        f"{name} drift antara prompts/ root dan wth_ai/prompts/"
    )


def test_load_prompt_independent_of_cwd(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    body = load_prompt(WTHConfig(), "supervisor_system.txt")
    assert len(body) > 1000


def test_load_prompt_honors_explicit_absolute_dir(tmp_path) -> None:
    custom = tmp_path / "custom_prompts"
    custom.mkdir()
    (custom / "supervisor_system.txt").write_text("CUSTOM-DOCTRINE", encoding="utf-8")
    cfg = WTHConfig()
    cfg.prompts_dir = str(custom)
    assert load_prompt(cfg, "supervisor_system.txt").startswith("CUSTOM-DOCTRINE")


def test_tui_init_is_lazy() -> None:
    """`import wth_ai.cli.tui` tidak memuat textual/App.

    Dicek di interpreter bersih (subprocess) supaya tidak bergantung urutan
    import suite: modul yang sudah ter-cache tidak memicu `__import__` lagi.
    """
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import wth_ai.cli.tui\n"
        "assert 'textual' not in sys.modules, 'textual dimuat eager'\n"
        "assert 'wth_ai.cli.tui.app' not in sys.modules, 'App dimuat eager'\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_SOURCE_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


def test_entry_finds_packaged_cli_when_root_missing() -> None:
    from wth_ai.cli import entry

    found = entry._find_main_script(None)
    assert found is not None
    assert found.name == "main_cli.py"
    assert found.is_file()
