"""Status card for REPL header."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.panel import Panel
from rich.text import Text

from wth_ai import __version__
from wth_ai.cli.ui.theme import SEP, STATUS_OFFLINE, STATUS_ONLINE, THEME

if TYPE_CHECKING:
    from wth_ai.config import WTHConfig
    from wth_ai.models import SessionState


def render_status_panel(
    state: SessionState,
    config: WTHConfig,
    *,
    tools_installed: int = 0,
    tools_total: int = 0,
    llm_online: bool = False,
    agent_phase: str = "idle",
    tool_count: int = 0,
) -> Panel:
    session = state.session_id[:8] if state.session_id else "—"
    target = state.scope.target or "unset"
    perm = state.permission_mode.value
    llm = "online" if llm_online else "required"
    cov = state.metadata.get("graph_coverage", {})
    cov_pct = cov.get("coverage_pct", 0) if isinstance(cov, dict) else 0
    verified = sum(
        1 for f in state.findings if isinstance(f, dict) and f.get("state") == "verified"
    )

    line1 = Text.assemble(
        (f"wth v{__version__}", "bold"),
        (f"  ·  target {target}", THEME["text"]),
        (f"  ·  perm {perm}", THEME["muted"]),
        (f"  ·  llm {llm}", THEME["accent"] if llm_online else THEME["error"]),
        (f"  ·  cov {cov_pct}%", THEME["recon"]),
    )
    line2 = Text.assemble(
        (f"session {session}", THEME["muted"]),
        (f"  ·  phase {agent_phase}", THEME["plan"]),
        (f"  ·  tools {tool_count}", THEME["muted"]),
        (f"  ·  ext {tools_installed}/{tools_total}", THEME["muted"]),
    )
    if verified:
        line2.append_text(Text(f"  ·  verified {verified}", style=THEME["verify"]))

    return Panel(Text.assemble(line1, "\n", line2), border_style=THEME["border"], title="status")


def render_status_line(
    state: SessionState,
    config: WTHConfig,
    *,
    tools_installed: int = 0,
    tools_total: int = 0,
    llm_online: bool = False,
    agent_phase: str = "idle",
    tool_count: int = 0,
) -> str:
    """Single-line unicode status for plain terminal REPL."""
    session = state.session_id[:8] if state.session_id else "—"
    target = state.scope.target or "unset"
    perm = state.permission_mode.value
    llm_dot = STATUS_ONLINE if llm_online else STATUS_OFFLINE
    llm = "online" if llm_online else "required"
    cov = state.metadata.get("graph_coverage", {})
    cov_pct = cov.get("coverage_pct", 0) if isinstance(cov, dict) else 0
    verified = sum(
        1 for f in state.findings if isinstance(f, dict) and f.get("state") == "verified"
    )

    parts = [
        f"wth v{__version__}",
        f"target {target}",
        f"perm {perm}",
        f"{llm_dot} llm {llm}",
        f"phase {agent_phase}",
        f"tools {tool_count}",
        f"ext {tools_installed}/{tools_total}",
        f"cov {cov_pct}%",
        f"session {session}",
    ]
    if verified:
        parts.append(f"verified {verified}")
    return SEP.join(parts)


def status_plain(
    state: SessionState,
    config: WTHConfig,
    **kwargs: Any,
) -> str:
    p = render_status_panel(state, config, **kwargs)
    from rich.console import Console
    from io import StringIO
    buf = StringIO()
    Console(file=buf, width=100, highlight=False).print(p)
    return buf.getvalue().strip()
