"""Codex-style status bar for WTH REPL."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wth_ai import __version__

if TYPE_CHECKING:
    from wth_ai.config import WTHConfig
    from wth_ai.models import SessionState


def render_status_bar(
    state: SessionState,
    config: WTHConfig,
    *,
    tools_installed: int = 0,
    tools_total: int = 0,
    llm_online: bool = False,
) -> str:
    session = state.session_id[:8] if state.session_id else "—"
    target = state.scope.target or "unset"
    phase = state.current_phase.value
    perm = state.permission_mode.value
    model = _short_model(config.models.planner)
    llm = "online" if llm_online else "required"
    tools = f"{tools_installed}/{tools_total}" if tools_total else "—"
    queued = len(state.task_queue)

    parts = [
        f"wth v{__version__}",
        f"session {session}",
        f"target {target}",
        f"phase {phase}",
        f"perm {perm}",
        f"model {model}",
        f"llm {llm}",
        f"tools {tools}",
    ]
    if queued:
        parts.append(f"queue {queued}")
    hyp = len(state.metadata.get("pre_hypotheses", []))
    chains = len(state.metadata.get("exploit_chains", []))
    verified = sum(1 for f in state.findings if isinstance(f, dict) and f.get("state") == "verified")
    if hyp or chains:
        parts.append(f"hyp {hyp}")
        parts.append(f"chains {chains}")
    cov = state.metadata.get("graph_coverage", {})
    if cov:
        parts.append(f"cov {cov.get('coverage_pct', 0)}%")
    if verified:
        parts.append(f"verified {verified}")
    return " · ".join(parts)


def _short_model(model_id: str) -> str:
    if "/" in model_id:
        name = model_id.split("/")[-1]
    else:
        name = model_id
    if ":" in name:
        name = name.split(":")[0]
    return name[:24]
