"""Bugfix dari transkrip operator: `/resume` turn kosong, `/effort` ke-hijack
menu generik, dan output chat terpotong yang tak bisa didiagnosis.

Bukti reproduksi (PTY sungguhan, `wth` + TUI, COLUMNS=120 LINES=45):
* `/resume` -> menu satu-item berlabel "id" (aksi `/resume id`); Esc -> turn
  kosong. Akar: `_subcommands_from_usage("/resume [id]")` menganggap `[id]`
  sebagai subcommand.
* `/findings` ternyata TERcetak ("No findings yet.") di log proses -- blank di
  layar user adalah artefak repaint TUI, bukan output hilang; tidak diubah.
* Panel "Shortcuts" setelah `/model` TIDAK mereproduksi: `/model` di PTY asli
  membuka picker 14 model; panel Shortcuts hanya dipanggil oleh tombol `?`.
  Tidak ada perubahan kode untuk itu.
* Output chat berhenti di tengah daftar ("Rekomendasi next step (pilih satu):")
  tapi `finish_reason` dibuang provider -> "terpotong" vs "selesai" tak bisa
  dibedakan. Sekarang direkam + ChatAgent memberi peringatan `/effort`.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_PLAIN", "1")

from wth_ai.cli.ui.menu import parse_subcommands  # noqa: E402


# ── 1) placeholder bukan subcommand ─────────────────────────────────────


def test_placeholder_argument_is_not_a_subcommand():
    assert parse_subcommands("/resume [id]") == []


def test_pipe_group_still_counts_as_subcommands():
    assert parse_subcommands("/config [show|path]") == ["show", "path"]
    assert len(parse_subcommands("/effort [low|medium|high|xhigh|ultra]")) == 5


def test_menu_never_opens_for_single_item_or_placeholder(monkeypatch):
    """`/resume` harus lolos langsung ke handler, bukan menu satu-item."""
    from wth_ai.cli.repl import WTHRepl
    from wth_ai.cli.ui import menu as menu_mod
    from wth_ai.platform.registry import get_platform_registry

    calls: list[str] = []
    monkeypatch.setattr(menu_mod, "interactive_available", lambda: True)
    monkeypatch.setattr(menu_mod, "run_menu", lambda *a, **k: calls.append("menu") or None)

    dummy = type("Dummy", (), {"SELF_INTERACTIVE": WTHRepl.SELF_INTERACTIVE})()

    assert WTHRepl._maybe_open_menu(dummy, "/resume") == "/resume"
    assert calls == [], "menu tidak boleh dibuka untuk placeholder argumen"

    # SELF_INTERACTIVE: picker sendiri, tanpa menu generik.
    assert WTHRepl._maybe_open_menu(dummy, "/effort") == "/effort"
    assert calls == []

    # Command dengan >= 2 subcommand nyata tetap membuka menu.
    registry = get_platform_registry()
    target = next(
        c for c in registry.commands
        if len(parse_subcommands(c.usage)) >= 2 and c.name not in WTHRepl.SELF_INTERACTIVE
    )
    choice = WTHRepl._maybe_open_menu(dummy, target.name)
    assert calls == ["menu"]
    assert choice is None  # run_menu spy mengembalikan None -> batal


def test_effort_is_self_interactive_so_its_own_picker_runs():
    from wth_ai.cli.repl import WTHRepl

    assert "/effort" in WTHRepl.SELF_INTERACTIVE


# ── 2) finish_reason direkam provider ────────────────────────────────────


class _FakeResponse:
    status_code = 200
    ok = True

    def __init__(self, payload: dict) -> None:
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:  # noqa: D102
        return None


def test_provider_records_finish_reason_length(monkeypatch):
    from wth_ai.llm.providers import openai_compat as oc

    payload = {
        "choices": [{"message": {"role": "assistant", "content": "terpotong di teng"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8192},
    }
    monkeypatch.setattr(oc.requests, "post", lambda *a, **k: _FakeResponse(payload))
    provider = oc.OpenAICompatProvider(provider_name="custom", api_key="k", base_url="https://example.test/v1")

    assert provider.chat("m", [{"role": "user", "content": "halo"}]) == "terpotong di teng"
    assert provider.last_finish_reason == "length"


def test_provider_records_normal_stop(monkeypatch):
    from wth_ai.llm.providers import openai_compat as oc

    payload = {
        "choices": [{"message": {"role": "assistant", "content": "lengkap."}, "finish_reason": "stop"}],
        "usage": {},
    }
    monkeypatch.setattr(oc.requests, "post", lambda *a, **k: _FakeResponse(payload))
    provider = oc.OpenAICompatProvider(provider_name="custom", api_key="k", base_url="https://example.test/v1")
    provider.chat("m", [{"role": "user", "content": "halo"}])
    assert provider.last_finish_reason == "stop"


# ── 3) ChatAgent memberi tahu operator ───────────────────────────────────


class _StubClient:
    def __init__(self, finish: str = "") -> None:
        self.last_finish_reason = finish


def _agent(finish: str):
    from wth_ai.agents.chat import ChatAgent
    from wth_ai.config import WTHConfig
    from wth_ai.skills.registry import SkillRegistry

    cfg = WTHConfig()
    skills = SkillRegistry(openclaw_path=cfg.skills.openclaw_path, local_path=cfg.skills.local_path)
    return ChatAgent(cfg, _StubClient(finish), skills)


def test_chat_answer_warns_when_truncated(monkeypatch):
    from wth_ai.models import SessionState

    agent = _agent("length")
    monkeypatch.setattr(agent, "_call", lambda *a, **k: ("Jawaban setengah jadi", "stub-model"))
    out = agent.respond("gimana hasilnya?", SessionState())
    assert "Respons terpotong" in out["raw"]
    assert "/effort high" in out["raw"]


def test_chat_answer_has_no_warning_when_complete(monkeypatch):
    from wth_ai.models import SessionState

    agent = _agent("stop")
    monkeypatch.setattr(agent, "_call", lambda *a, **k: ("Jawaban lengkap.", "stub-model"))
    out = agent.respond("halo", SessionState())
    assert "Respons terpotong" not in out["raw"]
