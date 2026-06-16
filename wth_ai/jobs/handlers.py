"""Default job handlers (mock/non-destructive).

These are intentionally safe placeholders. Fase 5 will wire the recon/executor
modules behind the same handler signature: ``handler(job, ctx) -> dict``.
"""

from __future__ import annotations

from typing import Any, Callable

# ctx exposes: ctx.log(str), ctx.progress(current, total, message), ctx.should_stop()
Handler = Callable[[Any, Any], dict[str, Any]]


def handler_recon(job: Any, ctx: Any) -> dict[str, Any]:
    ctx.log(f"recon start target={job.target}")
    ctx.progress(50, 100, "probing (mock)")
    if ctx.should_stop():
        return {"stopped": True}
    ctx.progress(100, 100, "done")
    ctx.log("recon complete (mock)")
    return {"type": "recon", "target": job.target, "summary": "mock recon complete", "findings": 0}


def handler_executor(job: Any, ctx: Any) -> dict[str, Any]:
    ctx.log("executor (mock, non-destructive)")
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
