"""Route inbound messages to cached orchestrator sessions."""

from __future__ import annotations

from typing import Any

from wth_ai.gateway.session_cache import SessionCache


class GatewayRouter:
    def __init__(self, cache: SessionCache) -> None:
        self.cache = cache

    def handle(self, session_key: str, text: str, *, fresh: bool = False) -> str:
        from wth_ai.cli.quit import SessionQuit

        orch = self.cache.get(session_key, fresh=fresh)
        try:
            return orch.handle_input(text)
        except SessionQuit as exc:
            return exc.message

    def status(self, session_key: str) -> dict[str, Any]:
        orch = self.cache.get(session_key)
        ts = orch.tool_registry.summary()
        ctx = getattr(orch, "_supervisor_ctx", None)
        return {
            "session_id": orch.state.session_id,
            "target": orch.state.scope.target or "",
            "permission": orch.state.permission_mode.value,
            "phase": getattr(ctx, "current_phase", "idle") if ctx else "idle",
            "llm_online": orch.client.agentic_available(),
            "tools_installed": ts["installed"],
            "tools_total": ts["total"],
            "task_queue": len(orch.state.task_queue),
            "active_skills": orch.state.metadata.get("active_skills", []),
        }

    def gateway_status(self, worker_status: dict[str, Any]) -> dict[str, Any]:
        return {
            "sessions": self.cache.status(),
            "worker": worker_status,
        }
