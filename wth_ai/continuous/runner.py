"""One proactive cycle: run adaptive autopilot for every configured target."""

from __future__ import annotations

from typing import Any

from wth_ai.hunt.autopilot import run_autopilot


def _strip_scheme(target: str) -> str:
    value = target.strip()
    if "://" in value:
        return value.split("://", 1)[1].split("/", 1)[0]
    return value.split("/", 1)[0]


def run_cycle(
    orch: Any,
    targets: list[str],
    *,
    steps: int = 6,
    cycles: int = 1,
    adaptive: bool = True,
) -> dict[str, Any]:
    """Run one proactive pass per target using the same audited probe path.

    Returns a compact summary that the daemon persists; no hidden executor.
    """
    from wth_ai.engagement.mode import is_no_limits

    bridge = getattr(orch, "terminal", None)
    if bridge is None:
        return {"error": "terminal bridge unavailable", "targets": {}}

    def probe(payload: dict[str, Any]) -> Any:
        return bridge.dispatch_tool("http_probe", payload, actor="supervisor", approved=False)

    no_limits = is_no_limits(getattr(orch, "config", None))
    summary: dict[str, Any] = {"targets": {}, "error": ""}
    for raw_target in targets:
        target = _strip_scheme(raw_target)
        if not target:
            continue
        previous_target = getattr(orch.state.scope, "target", "")
        orch.state.scope.target = target
        try:
            report = run_autopilot(
                target=target,
                run_probe=probe,
                state=orch.state,
                # Always finite for a background cycle: `0` means unbounded
                # inside run_autopilot, which would starve the daemon loop.
                max_cycles=max(1, cycles),
                max_steps_per_cycle=max(1, steps),
                destructive_allowed=no_limits or bool(getattr(orch.state.scope, "destructive_allowed", False)),
                adaptive=adaptive,
                adaptive_moves=4 if no_limits else 3,
                adaptive_budget=0 if no_limits else 12,
            )
            adaptive_steps = sum(1 for s in report.steps if s.kind == "adaptive")
            adaptive_wins = sum(1 for s in report.steps if s.kind == "adaptive" and s.changed)
            summary["targets"][target] = {
                "cycles": report.cycles,
                "steps": report.ran,
                "changed": report.changed,
                "adaptive_steps": adaptive_steps,
                "adaptive_wins": adaptive_wins,
                "stopped": report.stopped_reason,
                "destructive": report.destructive_used,
            }
        except Exception as exc:  # noqa: BLE001
            summary["targets"][target] = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            orch.state.scope.target = previous_target
    return summary


def summarize(summary: dict[str, Any]) -> str:
    parts = []
    for target, data in (summary.get("targets") or {}).items():
        if data.get("error"):
            parts.append(f"{target}: ERROR {data['error']}")
            continue
        parts.append(
            f"{target}: steps={data.get('steps', 0)} changed={data.get('changed', 0)} "
            f"adaptive={data.get('adaptive_steps', 0)}"
            f"/{data.get('adaptive_wins', 0)} win"
        )
    return "; ".join(parts) or "no targets"
