"""Regression tests — WU-106 (subset): interrupt-check di `AgentToolLoop`
(`/stop` benar-benar menghentikan loop yang sedang berjalan).

Bug asli (PRD-006/PRD-008, temuan second-pass critique fase arsitektur):
`SupervisorAgent._stopped` di-set oleh `.stop()` (`/stop` command) tapi TIDAK
PERNAH dibaca kode manapun -- diverifikasi ulang sebelum implementasi via
grep untuk pola atribut `._stopped` = HANYA 2 hasil, keduanya WRITE
(`__init__`, `.stop()`), NOL read. Untuk pengguna CLI tunggal-thread ini
nyaris tidak berefek (loop sudah selesai sebelum `/stop` sempat diproses),
TAPI untuk bot (Discord/Telegram) yang menjalankan `run_command()` di
background thread (`asyncio.to_thread`) sambil event loop tetap menerima
pesan lain, user BISA mengirim "/stop" sebagai pesan terpisah SAAT request
sebelumnya masih berjalan -- skenario ini genuinely reachable, bukan
hipotetis, dan sebelum fix ini sama sekali tidak py punya efek.

Fix: flag pindah ke `SupervisorContext.stopped` (shared di titik yang
BENAR-BENAR dibaca `AgentToolLoop`, bukan `SupervisorAgent` yang tidak
pernah diakses loop), dicek di AWAL setiap iterasi SEBELUM LLM call
(bukan setelah, supaya interupsi tidak menunggu 1 LLM call mahal lagi).
`SupervisorAgent.stop()` men-set KEDUANYA (`self._stopped` dipertahankan
untuk kompatibilitas kode lain yang mungkin masih membacanya, `ctx.stopped`
yang benar-benar dikonsumsi). Direset ke False di awal `SupervisorAgent.run()`
("Say anything to continue", sesuai pesan `.stop()` sendiri) -- tanpa reset
ini, SEKALI `/stop` dipanggil, SEMUA giliran berikutnya akan langsung
berhenti di iterasi pertama selamanya.

Scope decision (WORK-UNITS.md WU-106): HANYA interrupt-check in-process ini
yang diimplementasikan. `RunningActionRegistry` (kill proses via
`os.killpg`), flag persisten `runtime/killswitch.json`, entry point CLI/bot/
signal terpisah dari proses utama -- SEMUA belum dikerjakan, butuh integrasi
subprocess-registration di pipeline broker (WU-102's skeleton belum
melakukan ini) yang di luar scope WU ini.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.agents.supervisor_context import SupervisorContext
from wth_ai.agents.tool_loop import AgentToolLoop


class _StubBridge:
    def dispatch_tool(self, name, arguments, *, actor, approved=False):
        raise AssertionError("tool dispatch tidak boleh terpanggil setelah stop diaktifkan")


class _StubClient:
    def agentic_available(self) -> bool:
        return True


def _minimal_ctx() -> SupervisorContext:
    """Stand-in context minimal -- hanya field yang benar-benar dibaca
    AgentToolLoop untuk skenario ini (`stopped`)."""
    from wth_ai.models import SessionState

    return SupervisorContext(
        config=None, client=None, state=SessionState(), scope_mgr=None, permission_mgr=None,
        asset_graph=None, evidence_vault=None, findings_db=None, terminal=None,
    )


# ---------------------------------------------------------------------------
# REGRESI-GUARD utama -- loop benar-benar berhenti, bukan cuma flag di-set
# ---------------------------------------------------------------------------

def test_loop_stops_mid_run_when_stopped_flag_set():
    ctx = _minimal_ctx()

    def _fake_chat_with_tools(role, messages, tools, *, timeout):
        # Iterasi pertama: balas dengan tool_calls (loop akan lanjut normal),
        # TAPI sebagai side-effect set ctx.stopped=True -- mensimulasikan
        # user mengirim "/stop" via pesan terpisah SAAT loop sedang jalan.
        ctx.stopped = True
        return {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "function": {"name": "scope_show", "arguments": "{}"}},
        ]}}

    loop = AgentToolLoop(client=_StubClient(), bridge=_StubBridge(), max_iters=8, supervisor_ctx=ctx)
    loop._chat_with_tools = _fake_chat_with_tools

    result = loop.run("model-x", "system", "user goal", actor="supervisor")

    assert result["violations"] == ["stopped_by_user"]
    assert "Stopped by user" in result["content"] or result["grounded"] is not None


def test_loop_never_calls_llm_at_all_when_already_stopped_before_first_iteration():
    """Bukti reachability lebih kuat: kalau stopped SUDAH True sebelum
    run() dipanggil sama sekali (mis. /stop lalu langsung follow-up cepat),
    LLM tidak pernah dipanggil sekali pun."""
    ctx = _minimal_ctx()
    ctx.stopped = True
    call_count = {"n": 0}

    def _fake_chat_with_tools(role, messages, tools, *, timeout):
        call_count["n"] += 1
        return {"message": {"role": "assistant", "content": "should not reach here"}}

    loop = AgentToolLoop(client=_StubClient(), bridge=_StubBridge(), max_iters=8, supervisor_ctx=ctx)
    loop._chat_with_tools = _fake_chat_with_tools

    result = loop.run("model-x", "system", "user goal", actor="supervisor")

    assert call_count["n"] == 0
    assert result["violations"] == ["stopped_by_user"]


def test_loop_stops_before_max_iters_not_after():
    """Bukti kuantitatif: loop berhenti JAUH sebelum max_iters=8 begitu
    stopped di-set pada iterasi ke-2, bukan menghabiskan semua iterasi."""
    ctx = _minimal_ctx()
    call_count = {"n": 0}

    def _fake_chat_with_tools(role, messages, tools, *, timeout):
        call_count["n"] += 1
        if call_count["n"] == 2:
            ctx.stopped = True
        return {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": str(call_count["n"]), "function": {"name": "scope_show", "arguments": "{}"}},
        ]}}

    loop = AgentToolLoop(client=_StubClient(), bridge=_StubBridge(), max_iters=8, supervisor_ctx=ctx)
    loop._chat_with_tools = _fake_chat_with_tools
    loop._dispatch_tool = lambda name, args, *, actor, approved: "ok"

    result = loop.run("model-x", "system", "user goal", actor="supervisor")

    assert call_count["n"] == 2, f"seharusnya berhenti tepat setelah iterasi ke-2, malah {call_count['n']}"
    assert result["violations"] == ["stopped_by_user"]


# ---------------------------------------------------------------------------
# Compatibility -- loop tanpa supervisor_ctx (jalur executor) tidak terpengaruh
# ---------------------------------------------------------------------------

def test_loop_without_supervisor_ctx_unaffected_by_interrupt_check():
    """ExecutorAgent._execute_with_tools() TIDAK mengoper supervisor_ctx sama
    sekali -- pastikan None-guard di interrupt-check tidak crash jalur ini."""
    loop = AgentToolLoop(client=_StubClient(), bridge=_StubBridge(), max_iters=1, supervisor_ctx=None)
    loop._chat_with_tools = lambda role, messages, tools, *, timeout: {
        "message": {"role": "assistant", "content": "done, no tools"},
    }
    result = loop.run("model-x", "system", "user goal", actor="executor")
    assert result.get("violations") != ["stopped_by_user"]


# ---------------------------------------------------------------------------
# SupervisorAgent.stop()/run() -- sinkronisasi flag + reset "say anything to continue"
# ---------------------------------------------------------------------------

def test_supervisor_stop_sets_context_flag():
    from unittest.mock import MagicMock

    from wth_ai.agents.supervisor import SupervisorAgent
    from wth_ai.config import WTHConfig

    agent = SupervisorAgent(WTHConfig(), client=MagicMock())
    ctx = _minimal_ctx()
    agent.bind(ctx)
    agent.stop()
    assert ctx.stopped is True
    assert agent._stopped is True


def test_supervisor_run_resets_stopped_flag_for_new_turn():
    """Tanpa reset ini: sekali /stop dipanggil, SEMUA giliran berikutnya
    langsung berhenti di iterasi pertama selamanya -- regresi tersembunyi
    yang HARUS ditutup bersamaan dengan wiring interrupt-check itu sendiri."""
    from unittest.mock import MagicMock

    from wth_ai.agents.supervisor import SupervisorAgent
    from wth_ai.config import WTHConfig
    from wth_ai.models import SessionState

    client = MagicMock()
    client.agentic_available = lambda: False  # forces the WTH_TEST_MODE offline-fallback branch
    agent = SupervisorAgent(WTHConfig(), client=client)
    ctx = _minimal_ctx()
    agent.bind(ctx)
    agent.stop()
    assert ctx.stopped is True

    state = SessionState()
    agent.run("halo lagi", state)  # WTH_TEST_MODE offline fallback path

    assert ctx.stopped is False, "REGRESI: giliran baru tidak mereset stopped -- loop berikutnya akan macet permanen"
    assert agent._stopped is False
