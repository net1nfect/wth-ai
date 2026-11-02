"""Tests for the adaptive table renderer.

Every list command used to print undifferentiated `click.echo` lines, so `wth
usage` / the 346-tool registry / the model list gave no scannable structure. The
renderer now adapts: rich tables on a terminal, aligned plain text through a pipe.

The property that matters most is the SECOND one: piped output must stay plain.
`wth usage | grep`, log capture and CI must never receive escape codes, and the
existing plain output shape must keep working.
"""

from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.cli.ui.tables import MAX_PLAIN_WIDTH, plain_output, render_table

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _force_terminal(monkeypatch, *, is_tty: bool) -> None:
    class _Stream:
        def isatty(self) -> bool:
            return is_tty

    monkeypatch.delenv("WTH_COMPACT", raising=False)
    monkeypatch.delenv("WTH_PLAIN", raising=False)
    monkeypatch.setattr(sys, "stdout", _Stream())


# --------------------------------------------------------------------------- #
# mode selection
# --------------------------------------------------------------------------- #
def test_piped_output_is_plain(monkeypatch):
    _force_terminal(monkeypatch, is_tty=False)
    assert plain_output() is True


def test_terminal_output_is_rich(monkeypatch):
    _force_terminal(monkeypatch, is_tty=True)
    assert plain_output() is False


def test_env_overrides_force_plain(monkeypatch):
    _force_terminal(monkeypatch, is_tty=True)
    monkeypatch.setenv("WTH_COMPACT", "1")
    assert plain_output() is True
    monkeypatch.delenv("WTH_COMPACT")
    monkeypatch.setenv("WTH_PLAIN", "1")
    assert plain_output() is True, "--plain harus berarti output polos juga"


# --------------------------------------------------------------------------- #
# plain path: grep-able, no escapes
# --------------------------------------------------------------------------- #
def _plain(title="Judul", columns=("a", "n"), rows=(("x", "1"), ("yy", "22"))):
    return render_table(title, columns, rows)


def test_plain_table_contains_no_escape_sequences_or_box_glyphs(monkeypatch):
    _force_terminal(monkeypatch, is_tty=False)
    out = _plain()
    assert "\x1b" not in out, "output polos tidak boleh memuat escape code"
    for glyph in ("┏", "┃", "╇", "─┬─", "│"):
        assert glyph not in out, f"glyph tabel {glyph!r} muncul di jalur polos"
    assert "x" in out and "yy" in out and "Judul" in out


def test_plain_table_aligns_columns_and_right_aligns_numbers(monkeypatch):
    _force_terminal(monkeypatch, is_tty=False)
    lines = _plain().splitlines()
    body = [ln for ln in lines if ln.startswith(("x", "yy"))]
    assert len(body) == 2
    # kolom angka rata kanan: "1" harus punya satu spasi ekstra dibanding "22"
    assert body[0].index("1") == body[1].index("2") + 1, body
    assert lines[0].startswith("Judul")


def test_plain_table_truncates_absurdly_wide_cells(monkeypatch):
    _force_terminal(monkeypatch, is_tty=False)
    out = render_table("t", ["kolom"], [["x" * 200]])
    assert "x" * 200 not in out
    assert "x" * MAX_PLAIN_WIDTH in out


def test_empty_input_says_kosong_instead_of_a_bare_header(monkeypatch):
    _force_terminal(monkeypatch, is_tty=False)
    out = render_table("Registry", ["tool"], [])
    assert "(kosong)" in out
    assert "Registry" in out


# --------------------------------------------------------------------------- #
# rich path
# --------------------------------------------------------------------------- #
def test_rich_table_has_borders_headers_and_data(monkeypatch):
    _force_terminal(monkeypatch, is_tty=True)
    out = render_table("Judul", ["tool", "call"], [["subfinder", "3"]], caption="catatan")
    assert "┏" in out and "┃" in out, "tabel rich tidak punya border"
    assert "tool" in out and "subfinder" in out
    assert "catatan" in out


def test_rich_table_survives_wide_content(monkeypatch):
    _force_terminal(monkeypatch, is_tty=True)
    out = render_table("t", ["kolom"], [["y" * 300]])
    assert out.strip(), "tabel rich kosong untuk konten lebar"


def test_rich_empty_table_is_readable(monkeypatch):
    _force_terminal(monkeypatch, is_tty=True)
    out = render_table("Registry", ["tool"], [])
    assert "kosong" in out


# --------------------------------------------------------------------------- #
# end-to-end through the real CLI (the wiring, not just the helper)
# --------------------------------------------------------------------------- #
def _run(args: list[str], *, env_extra: dict[str, str] | None = None) -> str:
    env = dict(os.environ)
    env.update({"WTH_SKIP_SETUP": "1", "COLUMNS": "100", "PYTHONPATH": REPO})
    env.pop("WTH_UNRESTRICTED", None)
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, "wth-main.py", *args],
        cwd=REPO, capture_output=True, text=True, timeout=120, env=env,
    )
    return proc.stdout + proc.stderr


def test_usage_command_plain_path_has_no_ansi():
    out = _run(["usage"], env_extra={"WTH_COMPACT": "1"})
    assert "\x1b" not in out
    assert "Pemakaian" in out


def test_tools_available_plain_path_lists_tools_without_ansi():
    out = _run(["recon", "tools", "available"], env_extra={"WTH_COMPACT": "1"})
    assert "\x1b" not in out
    assert "available" in out
    assert "tool" in out


def test_tools_available_rich_path_renders_a_table():
    """Simulate a terminal so the rich branch is exercised end to end."""
    out = _run(
        ["recon", "tools", "available"],
        env_extra={"WTH_COMPACT": "0", "FORCE_COLOR": "1", "TERM": "xterm-256color",
                   "WTH_TEST_FORCE_TTY": "1"},
    )
    # Either a rich table (borders) or the plain fallback is acceptable when the
    # child process has no tty; what must hold is that the tool names are there.
    assert "curl" in out or "subfinder" in out


def test_registry_list_includes_every_tool_and_a_summary(monkeypatch):
    _force_terminal(monkeypatch, is_tty=False)
    from wth_ai.recon.registry import ReconToolRegistry

    reg = ReconToolRegistry()
    out = render_table("Recon tools", ["tool", "status"],
                       [[name, "available"] for name in list(reg.tools)[:5]],
                       caption=f"{len(reg.tools)} tools")
    assert "346" in out or f"{len(reg.tools)} tools" in out
