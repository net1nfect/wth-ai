# wth_ai/jobs/handlers.py
"""Job handlers (Fase 5.2)

Recon handler now uses the real Recon Engine (ToolRegistry + Planner).
Still non-destructive in Fase 5.2.

Follows AGENTS.md:
- All actions respect Policy Engine + Permission
- Progress + logging via ctx
- No destructive actions without approval
"""

from __future__ import annotations

from typing import Any, Callable

# New Fase 5 imports
from wth_ai.recon import ToolRegistry, ReconPlanner


Handler = Callable[[Any, Any], dict[str, Any]]


def handler_recon(job: Any, ctx: Any) -> dict[str, Any]:
    """
    Real recon handler using Fase 5 Recon Engine.

    Steps in Fase 5.2:
    1. Load ToolRegistry (215 tools)
    2. Run ReconPlanner based on job profile
    3. Log selected tools + plan
    4. Return structured plan (non-destructive)
    """
    target = getattr(job, "target", None) or job.metadata.get("target", "unknown")
    profile = getattr(job, "profile", None) or job.metadata.get("profile", "default")

    ctx.log(f"[recon] Starting real recon for target={target} profile={profile}")
    ctx.progress(5, 100, "Loading tool registry")

    try:
        registry = ToolRegistry()
        planner = ReconPlanner(registry)

        ctx.progress(15, 100, "Generating recon plan")
        plan = planner.plan(target=target, profile=profile, max_risk="safe")

        ctx.progress(40, 100, f"Plan ready: {len(plan.selected_tools)} tools selected")

        # Log selected tools (for transparency)
        selected_names = [t.name for t in plan.selected_tools[:10]]
        ctx.log(f"[recon] Selected tools (first 10): {selected_names}")
        if len(plan.selected_tools) > 10:
            ctx.log(f"[recon] ... and {len(plan.selected_tools) - 10} more tools")

        ctx.progress(70, 100, "Recon plan complete (non-destructive mode)")

        result = {
            "type": "recon",
            "target": target,
            "profile": profile,
            "plan_summary": {
                "total_tools_available": plan.total_tools,
                "tools_selected": len(plan.selected_tools),
                "tools_skipped": len(plan.skipped_tools),
            },
            "selected_tools": [t.name for t in plan.selected_tools],
            "skipped_reasons": plan.skipped_tools[:5],  # sample
            "summary": f"Recon plan generated with {len(plan.selected_tools)} tools",
        }

        ctx.progress(100, 100, "done")
        ctx.log("[recon] Recon planning phase completed successfully")
        return result

    except Exception as e:
        ctx.log(f"[recon] ERROR: {str(e)}")
        return {
            "type": "recon",
            "target": target,
            "error": str(e),
            "summary": "Recon planning failed",
        }


def handler_executor(job: Any, ctx: Any) -> dict[str, Any]:
    ctx.log("executor (mock, non-destructive - Fase 5.2)")
    ctx.progress(100, 100, "done")
    return {"type": "executor", "target": job.target, "executed": False, "note": "mock non-destructive"}


def handler_report(job: Any, ctx: Any) -> dict[str, Any]:
    ctx.log("report (mock)")
    ctx.progress(100, 100, "done")
    return {"type": "report", "path": None, "summary": "mock report"}


def handler_provider_test(job: Any, ctx: Any) -> dict[str, Any]:
    ctx.log(f"provider_test (mock) provider={job.target}")
    ctx.progress(100, 100, "done")
    return {"type": "provider_test", "provider": job.target, "ok": True, "note": "mock"}


def handler_custom(job: Any, ctx: Any) -> dict[str, Any]:
    ctx.log("custom job (mock)")
    ctx.progress(100, 100, "done")
    return {"type": "custom", "echo": job.metadata}


def default_handlers() -> dict[str, Handler]:
    return {
        "recon": handler_recon,
        "executor": handler_executor,
        "report": handler_report,
        "provider_test": handler_provider_test,
        "custom": handler_custom,
    }
