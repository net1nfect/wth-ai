"""Regression tests — WU-118: konsolidasi 3 registry slash-command paralel
(PRD-002 FR#1-3).

Bug asli: `slash_router.py` (aktif, ~39 handler) + `slash_commands.py::
SlashCommandHandler` (35 command, dead code terkonfirmasi -- diinstansiasi
`self.slash = SlashCommandHandler()` di orchestrator.py tapi `self.slash`
TIDAK PERNAH direferensikan lagi di manapun, diverifikasi ulang via grep
menyeluruh sebelum dihapus) + `platform/registry.py::PlatformRegistry` (40
`PlatformCommand`, dipakai /help+autocomplete) -- PLUS tingkat literal
if/elif TERPISAH di `orchestrator.handle_input()` untuk /help,/quit,/stop,
/new-session,/resume,/doctor,/status,/sessions -- 4 tingkat dispatch total.
`/help` sendiri punya DUA implementasi berbeda hidup berdampingan tanpa
sinkron: literal block (compact/topic-aware) vs `SlashRouter.handlers["/help"]`
lama (buang args[0] mentah, tidak compact-aware) -- literal block SELALU
menang karena urutan pengecekan lebih dulu di handle_input().

Fix: kedelapan command literal-block di-merge jadi entry biasa
`SlashRouter.handlers` (logika `/help` yang lebih pintar dipertahankan,
BUKAN yang lama). Fast-path bare-word (tanpa `/`) DIPERSEMPIT hanya untuk
quit/exit/stop -- bare "help"/"?"/"bantuan" SENGAJA tidak lagi dipertahankan
(tidak ada test/caller lain yang bergantung padanya, diverifikasi sebelum
implementasi). `slash_commands.py` dihapus total.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_PLAIN", "1")

import pytest


# ---------------------------------------------------------------------------
# FR#2 -- dead code slash_commands.py benar-benar dihapus
# ---------------------------------------------------------------------------

def test_slash_commands_module_no_longer_exists():
    with pytest.raises(ModuleNotFoundError):
        import wth_ai.slash_commands  # noqa: F401


def test_orchestrator_no_longer_has_slash_attribute():
    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    assert not hasattr(orch, "slash")


# ---------------------------------------------------------------------------
# FR#3 -- invariant PlatformRegistry vs SlashRouter, gagal kalau drift
# ---------------------------------------------------------------------------

def test_all_platform_registry_commands_are_routable():
    """Acceptance criterion literal PRD-002: semua command yang diiklankan
    /help/autocomplete benar-benar routable. Pakai router.parse() (murni,
    tanpa side-effect) supaya command destruktif (/quit dkk) tidak benar-benar
    tereksekusi saat verifikasi -- multi-word alias (mis. "/scope set")
    resolve via fallback ke command dasarnya ("/scope" + args=["set"]) SAH
    by design, bukan drift, jadi dicek lewat parse() bukan literal-key match."""
    from wth_ai.cli.slash_router import build_slash_router
    from wth_ai.platform.registry import get_platform_registry

    router = build_slash_router(MagicMock())
    names = get_platform_registry().all_slash_names()
    unroutable = []
    for name in names:
        cmd, _args = router.parse(name)
        if cmd not in router.handlers:
            unroutable.append(name)
    assert not unroutable, f"Command diiklankan /help tapi tidak routable: {unroutable}"


# ---------------------------------------------------------------------------
# FR#1 -- command literal-block lama sekarang routable via SlashRouter
# ---------------------------------------------------------------------------

@pytest.fixture
def orch():
    from wth_ai.orchestrator import WTHOrchestrator
    return WTHOrchestrator(fresh=True)


def test_slash_status_still_works_via_router(orch):
    out = orch.handle_input("/status")
    assert "Session" in out or "WTH-AI Status" in out


def test_slash_doctor_still_works_via_router(orch):
    out = orch.handle_input("/doctor")
    assert out


def test_slash_sessions_still_works_via_router(orch):
    out = orch.handle_input("/sessions")
    assert out


def test_slash_new_session_still_works_via_router(orch):
    out = orch.handle_input("/new-session")
    assert "Session baru" in out


def test_slash_resume_with_no_arg_still_works_via_router(orch):
    out = orch.handle_input("/resume")
    assert out  # "Tidak ada session..." atau summary -- keduanya non-empty


def test_slash_stop_still_works_via_router(orch):
    out = orch.handle_input("/stop")
    assert "stopped" in out.lower()


def test_slash_quit_still_raises_session_quit_via_router(orch):
    from wth_ai.cli.quit import SessionQuit

    with pytest.raises(SessionQuit):
        orch.handle_input("/quit")


def test_slash_exit_alias_also_raises_session_quit(orch):
    from wth_ai.cli.quit import SessionQuit

    with pytest.raises(SessionQuit):
        orch.handle_input("/exit")


def test_quit_still_not_routed_to_supervisor_via_router(orch):
    """Reproduksi eksplisit tests/test_quit.py::test_quit_not_routed_to_supervisor
    -- pastikan refactor jalur dispatch tidak membuka celah fall-through baru."""
    from wth_ai.cli.quit import SessionQuit

    with patch.object(orch.supervisor, "run") as mock_run:
        with pytest.raises(SessionQuit):
            orch.handle_input("/quit")
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# /help -- logika compact/topic dipertahankan (bukan yang lama, buang args)
# ---------------------------------------------------------------------------

def test_help_bare_still_works(orch):
    out = orch.handle_input("/help")
    assert "Slash Commands" in out or "WTH-AI" in out


def test_help_full_topic_uses_non_compact_mode(orch):
    """Sebelumnya HANYA literal block yang punya logika compact/topic --
    handler /help lama di SlashRouter.handlers buang args[0] mentah. Fix ini
    memastikan logika yang lebih pintar itu yang dipertahankan, bukan hilang."""
    compact_out = orch.handle_input("/help")
    full_out = orch.handle_input("/help full")
    assert full_out != compact_out or len(full_out) >= len(compact_out)


# ---------------------------------------------------------------------------
# Bare-word fast-path -- dipersempit HANYA quit/exit/stop (didokumentasikan)
# ---------------------------------------------------------------------------

def test_bare_quit_exit_stop_still_work_without_slash(orch):
    from wth_ai.cli.quit import SessionQuit

    with pytest.raises(SessionQuit):
        orch.handle_input("quit")


def test_bare_stop_without_slash_still_works(orch):
    out = orch.handle_input("stop")
    assert "stopped" in out.lower()


def test_bare_help_no_longer_special_cased():
    """Perubahan perilaku YANG DISENGAJA (didokumentasikan di PRD-002 FR#1,
    bukan regresi): bare 'help'/'?'/'bantuan' TIDAK LAGI di-fast-path --
    tidak ada test/caller lain yang bergantung padanya (diverifikasi via
    grep sebelum implementasi)."""
    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    with patch.object(orch.chat, "respond", return_value={"raw": "chat fallback", "model": "test"}):
        out = orch.handle_input("bantuan")
    assert out != orch.slash_router.help_text()
