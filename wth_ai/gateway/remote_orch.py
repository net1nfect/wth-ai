"""Remote orchestrator proxy for TUI when connected to gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from wth_ai.config import WTHConfig
from wth_ai.gateway.client import GatewayClient
from wth_ai.models import PermissionMode, SessionState
from wth_ai.tools.registry import ToolRegistry


@dataclass
class RemoteOrchestrator:
    """Minimal orchestrator surface for TUI over gateway RPC."""

    gateway: GatewayClient
    config: WTHConfig = field(default_factory=WTHConfig.load)
    state: SessionState = field(default_factory=SessionState)
    tool_registry: ToolRegistry = field(default_factory=ToolRegistry)
    _supervisor_ctx: Any = None
    _ui_bus: Any = None
    _tool_trace: Any = None
    _stream_callback: Any = None

    def __post_init__(self) -> None:
        self._refresh_state()

    def _refresh_state(self) -> None:
        st = self.gateway.get_status()
        self.state.session_id = st.get("session_id", self.state.session_id)
        setattr(self.state.scope, "target", st.get("target") or None)
        try:
            self.state.permission_mode = PermissionMode(st.get("permission", "read_only"))
        except ValueError:
            pass
        self.state.metadata["active_skills"] = st.get("active_skills", [])
        if self._supervisor_ctx is None:
            self._supervisor_ctx = SimpleNamespace(
                current_phase=st.get("phase", "idle"),
                tool_count=0,
                callbacks=None,
            )
        else:
            self._supervisor_ctx.current_phase = st.get("phase", "idle")

    @property
    def client(self) -> Any:
        gw = self.gateway
        online = False
        try:
            st = gw.get_status()
            online = bool(st.get("llm_online"))
        except Exception:
            pass
        avail = lambda: online
        agentic = lambda: online
        ns = SimpleNamespace(available=avail, agentic_available=agentic)
        return ns

    def handle_input(self, text: str) -> str:
        lower = text.strip().lower()
        if lower in {"/quit", "/exit", "quit", "exit"}:
            msg = "✓ WTH-AI ditutup"
            try:
                msg = self.gateway.handle_input("/quit") or msg
            except Exception:
                pass
            from wth_ai.cli.quit import SessionQuit
            raise SessionQuit(msg)
        result = self.gateway.handle_input(text)
        self._refresh_state()
        return result

    def shutdown(self, *, reason: str = "quit") -> str:
        return "✓ disconnected"

    def request_quit(self) -> None:
        from wth_ai.cli.quit import SessionQuit
        raise SessionQuit(self.shutdown())

    def run_interactive(self, *, local: bool = False) -> None:
        from wth_ai.orchestrator import WTHOrchestrator
        if local:
            WTHOrchestrator(self.config).run_interactive(local=True)
            return
        from wth_ai.cli.terminal_shell import run_terminal_shell
        run_terminal_shell(self)

    def set_stream_callback(self, cb: Any) -> None:
        self._stream_callback = cb
