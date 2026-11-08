"""P2-6: README's permission table must describe the presets that exist.

The table listed `read_only|standard|elevated|full_lab` and said `elevated`
still needs `/approve`. `elevated` resolves to the `auto` preset, which turns
approval OFF -- a docs table promising a confirmation the code does not ask for
is a safety bug, not a cosmetic one.
"""

from __future__ import annotations

import os
import pathlib
import re

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.permission_presets import ALIASES, PRESETS

README = pathlib.Path(__file__).resolve().parents[1] / "README.md"


def _rows() -> list[str]:
    text = README.read_text()
    start = text.index("## Permission Modes")
    end = text.index("\n## ", start + 1)
    return [ln for ln in text[start:end].splitlines() if ln.startswith("| `")]


def _names() -> list[str]:
    return [re.search(r"`([^`]+)`", row).group(1) for row in _rows()]


def test_readme_permission_table_lists_the_real_presets():
    names = set(_names())
    assert names, "tabel permission tidak ditemukan di README"
    unknown = sorted(n for n in names if n not in PRESETS and n not in ALIASES)
    assert not unknown, f"README menyebut mode yang tidak ada: {unknown}"
    missing = sorted(set(PRESETS) - names)
    assert not missing, f"preset tidak terdokumentasi di README: {missing}"


def test_readme_does_not_promise_an_approval_the_code_skips():
    for row in _rows():
        name = re.search(r"`([^`]+)`", row).group(1)
        preset = PRESETS.get(ALIASES.get(name, name))
        if preset is None or not preset.auto_approve:
            continue
        assert "/approve" not in row, (
            f"README bilang mode `{name}` butuh /approve, tapi preset "
            f"`{preset.name}` menyalakan auto_approve"
        )


# --------------------------------------------------------------------------- #
# P2-6: the command tables are a sample, and must say so with a real number
# --------------------------------------------------------------------------- #
def _table_rows(heading: str, prefix: str) -> int:
    text = README.read_text()
    start = text.index(heading)
    end = text.index("\n## ", start + 1)
    return sum(1 for ln in text[start:end].splitlines() if ln.startswith(prefix))


def _real_slash_commands() -> int:
    from wth_ai.platform.registry import get_platform_registry

    return len(get_platform_registry().commands)


def test_readme_slash_table_says_how_many_commands_exist():
    """11 rows next to 58 registered commands reads as "there are 11", so an
    operator never discovers `/engage`, `/cron`, `/eval`, `/payloads`…"""
    listed = _table_rows("### Slash command", "| `/")
    real = _real_slash_commands()
    assert 0 < listed < real, f"tabel slash {listed}, registry {real}"
    text = README.read_text()
    assert str(real) in text, f"README tidak menyebut jumlah perintah nyata ({real})"


def test_readme_documents_one_launcher_name():
    """P2-10/D5 membalik keputusan alias: `wth-ai` satu-satunya nama, `wth`
    dihapus. README tidak boleh lagi menyatakan keduanya setara.

    P1-8/D3 removes `[project.scripts]` on purpose (a wheel `bin/wth-ai` has no
    `wth-ai-main.py` beside it), so `scripts/install.sh` is the source of truth.
    """
    root = README.parent
    installer = (root / "scripts" / "install.sh").read_text()
    assert "for name in wth wth-ai" not in installer, "installer masih memasang alias"

    text = README.read_text()
    alias_line = next(
        (ln for ln in text.splitlines()
         if "`wth`" in ln and "`wth-ai`" in ln and "adalah alias" in ln.lower()),
        None,
    )
    assert alias_line is None, f"README masih menyatakan alias: {alias_line}"
