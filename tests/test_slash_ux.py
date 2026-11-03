"""UX pass — palette `/`, saran typo, `/help <kategori>`, menu ber-alur kerja.

Latar (semua TERUKUR dari sesi operator):
* mengetik `/` di mode plain/bot dijawab "Unknown command `/`" -- buntu;
* salah ketik (`/rcon`) dijawab "Unknown command ... Try /help" tanpa SATU pun
  saran nama yang mirip;
* `/help <kategori>` diam-diam menampilkan compact biasa -- cabang `cat` di
  SlashRouter.help_text() TIDAK PERNAH reachable (compact selalu True untuk
  topic non-keyword, dan cat di-nol-kan begitu compact True);
* menu `/` menampilkan kategori polos ("hunt", "meta", ...) berdeskripsi
  "N command" tanpa titik masuk.

Perbaikan diuji di sini; invariant lama (registry<->router) tetap di file lain.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_PLAIN", "1")

from unittest.mock import MagicMock  # noqa: E402

from wth_ai.cli.slash_router import build_slash_router  # noqa: E402
from wth_ai.platform.registry import get_platform_registry  # noqa: E402


def _router():
    return build_slash_router(MagicMock())


# ── bare "/" ─────────────────────────────────────────────────────────────


def test_bare_slash_returns_palette_not_error():
    out = _router().route("/")
    assert "Unknown command" not in out
    assert "MULAI DI SINI" in out
    assert "/target" in out and "/hunt" in out


def test_bare_slash_via_orchestrator():
    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/")
    assert "Unknown command" not in out
    assert "MULAI DI SINI" in out


# ── unknown command + saran ──────────────────────────────────────────────


def test_typo_command_suggests_close_names():
    out = _router().route("/rcon")
    assert "tidak dikenal" in out
    assert "/recon" in out, out


def test_typo_command_via_orchestrator():
    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/permisson")
    assert "/permission" in out, out


def test_far_typo_still_points_to_help_and_menu():
    out = _router().route("/zzzznope")
    assert "/help" in out


def test_suggestions_rank_prefix_first():
    reg = get_platform_registry()
    picks = reg.suggestions("/recon", limit=8)
    assert picks and picks[0] == "/recon"
    assert "/recon plan" in picks


# ── /help <kategori> benar-benar kategori ────────────────────────────────


def test_help_category_renders_that_category():
    out = _router().route("/help recon")
    assert "## recon" in out
    assert "usage:" in out
    assert "/recon quick" in out


def test_help_unknown_category_falls_back_with_valid_list():
    out = _router().route("/help bogus")
    assert "tidak ada" in out
    assert "setup" in out


def test_help_full_is_full_list():
    out = _router().route("/help full")
    assert "# WTH-AI Slash Commands" in out


def test_compact_help_keeps_key_sections():
    text = get_platform_registry().help_text(compact=True)
    for token in ("MULAI DI SINI", "Skills", "MCP", "/mcp", "/quit"):
        assert token in text, token


# ── menu ber-alur kerja ──────────────────────────────────────────────────


def test_menu_starts_with_start_here_and_workflow_labels():
    from wth_ai.cli.ui.menu import build_command_menu

    tree = build_command_menu()
    labels = [t.label for t in tree]
    assert labels[0] == "\u25b6 mulai di sini"
    assert "Recon" in labels and "Hunt" in labels and "MCP" in labels
    assert labels.index("Recon") < labels.index("Hunt")


def test_menu_category_descriptions_are_human():
    from wth_ai.cli.ui.menu import build_command_menu

    tree = build_command_menu()
    recon = next(t for t in tree if t.label == "Recon")
    assert "petakan attack surface" in recon.description
    assert "command" in recon.description


def test_menu_start_here_children_are_real_commands():
    from wth_ai.cli.ui.menu import build_command_menu
    from wth_ai.platform.registry import get_platform_registry

    tree = build_command_menu()
    names = set(get_platform_registry().all_slash_names())
    start = tree[0]
    assert start.is_submenu
    actions = [c.action for c in start.children]
    for action in ("/target", "/scope", "/permission", "/recon", "/hunt"):
        assert action in actions
        assert action in names


# ── completer memakai saran yang sama ────────────────────────────────────


def test_completer_offers_close_names_on_typo():
    from prompt_toolkit.document import Document

    from wth_ai.cli.tui.widgets.slash_completer import SlashCompleter

    completions = list(SlashCompleter().get_completions(Document(text="/rcon", cursor_position=5), None))
    # `display` adalah FormattedText ber-styling; yang O(n) bandingkan adalah `text`.
    assert any(getattr(c, "text", "") == "/recon" for c in completions), [c.text for c in completions]
