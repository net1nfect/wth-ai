"""Supervisor agent — single entry point for full agentic hunt."""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from wth_ai.agents import BaseLLMAgent, load_prompt
from wth_ai.agents.supervisor_context import SupervisorCallbacks, SupervisorContext
from wth_ai.agents.supervisor_dispatch import dispatch_supervisor_tool
from wth_ai.agents.tool_loop import AgentToolLoop
from wth_ai.config import WTHConfig
from wth_ai.llm.errors import LLMUnavailableError
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.models import AssessmentPhase, SessionState


HELP_TEXT = """# WTH-AI Agent

Chat-driven security research. Setup via slash commands:

- `/target example.com` or `/target https://host/path`
- `/model list` · `/model set planner Hacker`
- `/permission elevated` (alias `active_test`) · `/mode bounty`
- `/recon` · `/hunt` · `/findings` · `/report`

Commands: `/help` · `/stop`
"""


class SupervisorAgent(BaseLLMAgent):
    """Autonomous supervisor using ReAct tool loop."""

    def __init__(self, config: WTHConfig, client: LLMProviderRouter) -> None:
        super().__init__(config, client)
        self.system_prompt = load_prompt(config, "supervisor_system.txt")
        self._ctx: SupervisorContext | None = None
        self._stopped = False
        self._pending_approval: str | None = None

    def bind(self, ctx: SupervisorContext) -> None:
        self._ctx = ctx

    def help_text(self) -> str:
        return HELP_TEXT

    def stop(self) -> str:
        self._stopped = True
        if self._ctx:
            # WU-106: ctx.stopped is what AgentToolLoop actually reads --
            # self._stopped alone was write-only (set here, never read
            # anywhere) before this WU. Kept in sync for any external code
            # still inspecting SupervisorAgent._stopped directly.
            self._ctx.stopped = True
            self._ctx.state.current_phase = AssessmentPhase.STOPPED
        # F1/WU-106 (remainder): kill-switch proses. `ctx.stopped` hanya
        # menghentikan ITERASI berikutnya -- subprocess yang sedang berjalan
        # butuh killpg nyata (RunningActionRegistry). Tidak pernah raise:
        # cleanup tidak boleh mengganggu respons /stop.
        try:
            from wth_ai.running_actions import get_running_actions

            get_running_actions().kill_all()
        except Exception:  # noqa: BLE001
            pass
        return "Agent stopped. Say anything to continue or `/help`."

    def run(
        self,
        user_input: str,
        state: SessionState,
        *,
        callbacks: SupervisorCallbacks | None = None,
        persist_fn: Callable[[], None] | None = None,
    ) -> str:
        if self._ctx is None:
            return "Supervisor not initialized"
        self._ctx.state = state
        # WU-106: "Say anything to continue" (stop()'s own return message) --
        # a NEW user turn clears a previous /stop. Known narrow race (not
        # hardened further, this is best-effort UX not a security boundary):
        # if a stop for turn N arrives while turn N+1 is already inside this
        # reset, turn N+1 could clear it before turn N's loop observes it.
        self._stopped = False
        self._ctx.stopped = False
        if callbacks:
            self._ctx.callbacks = callbacks
        if persist_fn:
            self._ctx.persist_fn = persist_fn

        text = user_input.strip()
        if not text:
            return ""

        if self._handle_approval(text):
            return self._run_loop("User approved. Continue with the pending action.", state)

        if not self.client.agentic_available():
            if os.environ.get("WTH_TEST_MODE") == "1":
                return self._offline_fallback(user_input, state)
            raise LLMUnavailableError(
                "LLM required for agentic mode. Run `wth setup` to configure API key and models."
            )

        context = self._build_context_block(state)
        prompt = f"{context}\n\nUser: {text}"
        state.conversation.append({"role": "user", "content": text})
        result = self._run_loop(prompt, state)
        state.conversation.append({"role": "assistant", "content": result[:8000]})
        return result

    def _handle_approval(self, text: str) -> bool:
        lower = text.lower().strip()
        if lower in {"approve", "yes", "approved", "lanjut"} or lower.startswith("approve "):
            if self._ctx:
                self._ctx.approved_tasks.add(self._pending_approval or "shell")
            self._pending_approval = None
            return True
        return False

    def _run_loop(self, prompt: str, state: SessionState) -> str:
        assert self._ctx is not None
        loop = AgentToolLoop(
            self.client,
            self._ctx.terminal,
            graph=self._ctx.asset_graph,
            vault=self._ctx.evidence_vault,
            max_iters=self.config.agent.max_iters,
            supervisor_ctx=self._ctx,
            on_token=self._ctx.callbacks.on_token,
        )
        on_approval = self._ctx.callbacks.on_approval_required
        self._ctx.callbacks.on_approval_required = lambda tid: self._on_approval(tid, on_approval)

        started = time.time()
        # The approval the operator just granted applies to THIS turn only.
        # `approved_tasks` used to be read but never cleared, so a single "yes"
        # left `approved=True` on every dispatch of every later turn for the
        # rest of the session -- a standing bypass of the human-approval
        # dimension instead of approval of the one action that was pending.
        # (`orchestrator._approved_tasks`, the `/execute` path, is separate and
        # untouched.)
        turn_approved = bool(self._ctx.approved_tasks)
        result = loop.run(
            self.config.models.planner,
            self.system_prompt,
            prompt,
            actor="supervisor",
            backup=self.config.models.planner_backup,
            timeout=self.config.models.planner_timeout_seconds,
            approved=turn_approved,
        )
        if turn_approved:
            self._ctx.approved_tasks.clear()
        elapsed = round(time.time() - started, 1)
        content = (result.get("content") or "").strip()
        if not content:
            if self._ctx.tool_count:
                content = f"Selesai ({self._ctx.tool_count} tool calls). Cek `/target` atau `/status`."
            else:
                content = (
                    "Model tidak mengembalikan teks. Gunakan slash commands:\n"
                    "  `/target https://host/path` · `/model list` · `/help`"
                )
        model = result.get("model", "")
        meta = f"\n\n---\n*model: {model} · tools: {self._ctx.tool_count} · {elapsed}s · grounded: {result.get('grounded', True)}*"
        return content + meta

    def _on_approval(self, task_id: str, chained: Callable[[str], None] | None) -> None:
        self._pending_approval = task_id
        if chained:
            chained(task_id)

    def _build_context_block(self, state: SessionState) -> str:
        """WU-111 (ADR-004): migrated to `ContextBuilder` -- same sources
        (action_memory + md_context) and same unconditional session-summary
        preamble as the hand-rolled version this replaces, byte-for-byte
        equivalent output for this call site."""
        from wth_ai.context.builder import ContextBuilder

        builder = ContextBuilder(
            action_memory=self._ctx.action_memory if self._ctx else None,
            asset_graph=self._ctx.asset_graph if self._ctx else None,
        )
        return builder.build(state, sources=["action_memory", "md_context"])

    def _offline_fallback(self, text: str, state: SessionState) -> str:
        from wth_ai.scope_intent import parse_scope_intent

        args = parse_scope_intent(text)
        if args:
            dispatch_supervisor_tool(self._ctx, "scope_set", args, approved=True)
            target = args.get("target", "")
            return f"Scope set to {target} (offline test mode)"
        if self._ctx and self._ctx.recon_bridge:
            tools = self._ctx.recon_bridge.list_tools(available_only=True)[:5]
            return f"LLM offline. Available recon tools: {', '.join(t['name'] for t in tools)}"
        return "LLM offline (test mode). Configure provider via `wth setup`."
