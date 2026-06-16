"""ReAct tool-use loop for grounded agent execution."""

from __future__ import annotations

import json
from typing import Any, Callable

from wth_ai.agents.grounding import GroundingFirewall
from wth_ai.agents.tool_registry import tools_for_actor
from wth_ai.evidence.vault import EvidenceVault
from wth_ai.graph.asset_graph import AssetGraph
from wth_ai.llm.errors import LLMUnavailableError
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.terminal.bridge import AgentTerminalBridge


class AgentToolLoop:
    """Run LLM with tool calls until final grounded response."""

    def __init__(
        self,
        client: LLMProviderRouter,
        bridge: AgentTerminalBridge,
        *,
        graph: AssetGraph | None = None,
        vault: EvidenceVault | None = None,
        max_iters: int = 8,
    ) -> None:
        self.client = client
        self.bridge = bridge
        self.graph = graph
        self.vault = vault
        self.max_iters = max_iters
        self.grounding = GroundingFirewall()

    def run(
        self,
        model: str,
        system: str,
        user: str,
        *,
        actor: str = "executor",
        backup: str | None = None,
        timeout: int = 120,
        approved: bool = False,
    ) -> dict[str, Any]:
        if not self.client.available():
            import os
            if os.environ.get("WTH_TEST_MODE") == "1":
                return {"content": "LLM offline", "transcripts": [], "grounded": True}
            raise LLMUnavailableError("LLM tidak tersedia untuk tool loop. Jalankan `wth-ai setup`.")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        tools = tools_for_actor(actor)
        transcripts: list[str] = []

        for _ in range(self.max_iters):
            data = self.client.chat_with_tools(
                model, messages, tools=tools, backup=backup, timeout=timeout,
            )
            msg = data.get("message", {})
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                content = msg.get("content") or ""
                gr = self.grounding.validate_claims(content, transcript_ids=transcripts)
                return {
                    "content": gr.text,
                    "transcripts": transcripts,
                    "grounded": gr.grounded,
                    "violations": gr.violations,
                    "model": data.get("model", model),
                }

            messages.append(msg)
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result_text = self._dispatch_tool(name, args, actor=actor, approved=approved)
                if name in ("http_probe", "run_shell") and "[TX:" in result_text:
                    tid = result_text.split("[TX:")[1].split("]")[0]
                    transcripts.append(tid)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", name),
                    "content": result_text,
                })

        gr = self.grounding.validate_claims("Tool loop max iterations reached", transcript_ids=transcripts)
        return {"content": gr.text, "transcripts": transcripts, "grounded": False, "violations": ["max_iters"]}

    def _dispatch_tool(self, name: str, args: dict[str, Any], *, actor: str, approved: bool) -> str:
        if name == "query_graph":
            if self.graph and self.graph.nodes:
                return self.graph.export_for_planner(limit=40)
            return "Graph empty — run /recon first"

        if name == "store_evidence":
            tid = args.get("transcript_id", "")
            fid = args.get("finding_id", tid)
            if self.vault and tid:
                tx = self.bridge.transcripts.get(tid)
                if tx:
                    self.vault.store(
                        finding_id=fid,
                        request={"method": tx.get("method", "GET"), "url": tx.get("url") or tx.get("command", "")},
                        response={"status": tx.get("status_code", tx.get("exit_code")), "body": tx.get("stdout", "")[:2000]},
                        evidence_schema={"type": args.get("vuln_class", "unknown")},
                    )
                    return f"Stored evidence [EV linked to TX:{tid}]"
            return "No vault or transcript"

        if name in ("http_probe", "run_shell"):
            result = self.bridge.dispatch_tool(name, args, actor=actor, approved=approved)
            return result.summary() + "\n" + (result.stdout[:3000] if result.stdout else result.stderr[:500])

        return f"Unknown tool: {name}"
