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
            self._ctx.state.current_phase = AssessmentPhase.STOPPED
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
        result = loop.run(
            self.config.models.planner,
            self.system_prompt,
            prompt,
            actor="supervisor",
            backup=self.config.models.planner_backup,
            timeout=self.config.models.planner_timeout_seconds,
            approved=bool(self._ctx.approved_tasks),
        )
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
        cov = self._ctx.asset_graph.coverage() if self._ctx else {}
        from wth_ai.memory.md_context import context_block as md_block
        md = md_block()
        parts = [
            f"Session: {state.session_id}",
            f"Target: {state.scope.target or 'unset'}",
            f"Authorized: {state.scope.authorized}",
            f"Permission: {state.permission_mode.value}",
            f"Phase: {state.current_phase.value}",
            f"Graph nodes: {cov.get('nodes', 0)} coverage: {cov.get('coverage_pct', 0)}%",
            f"Queued tasks: {len(state.task_queue)}",
        ]
        if self._ctx and self._ctx.action_memory:
            am = self._ctx.action_memory.context_block()
            if am:
                parts.append(am)
        if md:
            parts.append(md)
        return "\n".join(parts)

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
