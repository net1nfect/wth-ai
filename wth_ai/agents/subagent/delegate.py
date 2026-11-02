"""delegate_task — spawn isolated subagent (depth-limited)."""

from __future__ import annotations

from typing import Any

from wth_ai.agents.subagent.registry import MAX_DEPTH, get_spec
from wth_ai.agents.subagent.runner import SubagentRunner


def delegate_task(orch: Any, agent_id: str, prompt: str, *, depth: int = 0) -> str:
    if depth >= MAX_DEPTH:
        return "ERROR: subagent depth limit reached"
    spec = get_spec(agent_id)
    if not spec:
        return f"ERROR: unknown subagent `{agent_id}`"
    runner = SubagentRunner(orch, depth=depth + 1)
    return runner.run(agent_id, prompt)
