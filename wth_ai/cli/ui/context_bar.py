"""Context bar — quick model/provider/memory status for REPL."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def _host(base_url: str | None) -> str:
    if not base_url:
        return ""
    try:
        p = urlparse(base_url)
        return p.netloc or p.path or ""
    except Exception:
        return base_url


def _estimate_tokens(conversation: list[dict[str, Any]] | None) -> int:
    # Heuristic: ~4 chars/token. Good enough for a CLI status bar.
    if not conversation:
        return 0
    chars = 0
    for m in conversation[-30:]:
        chars += len(str(m.get("content", "")))
    return max(0, chars // 4)


def render_context_bar(orchestrator: Any) -> str:
    cfg = getattr(orchestrator, "config", None)
    ui = getattr(cfg, "ui", None) if cfg else None
    agent = getattr(cfg, "agent", None) if cfg else None
    mode = getattr(agent, "mode", "hybrid") if agent else "hybrid"
    activity_mode = getattr(ui, "activity_mode", "live") if ui else "live"

    rc = orchestrator.client.role_config("planner")
    model, _ = orchestrator.client.model_for_role("planner")
    provider = orchestrator.client.provider_for_role("planner")
    ctx_len = provider.get_context_length(model)
    ctx_est = _estimate_tokens(getattr(orchestrator.state, "conversation", None))
    # P2-3: an empty conversation has nothing to measure. `ctx 0` reads as
    # "0 tokens used" when the truth is "not measured yet"; omit the field the
    # way `theme.budget_bar` returns "" for a limit of 0.
    ctx_bit = f"ctx {ctx_est}" + (f"/{ctx_len}" if ctx_len else "") if ctx_est else ""

    usage = orchestrator.state.metadata.get("last_llm_usage", {}) if hasattr(orchestrator, "state") else {}
    usage_bit = ""
    if isinstance(usage, dict) and usage:
        inp = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        out = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        if inp or out:
            usage_bit = f"usage {inp}+{out}"

    host = _host(getattr(provider, "base_url", None))
    parts = [
        ctx_bit,
        f"planner {model}",
        f"{rc.type}{(' @ ' + host) if host else ''}",
        f"mode {mode}",
        f"activity {activity_mode}",
    ]
    if usage_bit:
        parts.append(usage_bit)
    return " · ".join(p for p in parts if p)

