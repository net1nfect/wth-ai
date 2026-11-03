"""Minimal policy engine for job admission (Fase 4, WU-103).

Non-destructive, safe-by-default. Rejects out-of-policy jobs before they enter
the queue. Designed to be extended with richer scope/rate rules later.

WU-103 (PRD-005 FR#4) scope decision: the PRD's literal mechanism --
"REFACTOR jadi coarse pre-filter yang memanggil evaluate_intent() dengan
ActionIntent(tool_name=f'job.{type}', ...)" -- is NOT safely implementable
as written. `evaluate_intent()` resolves its `tool` argument against real
`PermissionProfile` capability entries (`policy/profiles_builtin.py`),
which enumerate actual executable tool names; a synthetic token like
"job.executor" matches none of them, so `cap is None` and EVERY job type
(including "recon", admitted successfully today) would start being
rejected outright unless matching synthetic capability entries were added
to all 5 built-in profiles purely to make a pre-filter pass -- scope creep
onto a shared, security-sensitive file for pseudo-tools that don't
correspond to anything a shell ever executes, and it wouldn't actually
unify anything (the "same" evaluate_intent() call would need a
special-cased resolution path just for job types). The underlying goal --
catch an under-privileged job before it's admitted, not just after it's
dequeued -- is delivered instead via `permission_mode` below, reusing the
SAME `PermissionMode`/`PERMISSION_LEVEL` ordering `permission_manager.py`
and `jobs/orchestrator_bridge.py::run_executor_job()` already enforce
downstream (`if perm.mode == PermissionMode.READ_ONLY_RECON: blocked`) --
moved earlier so an obviously-doomed job never occupies a job_id/queue
slot. `evaluate_intent()` unification stays the honest target for command-
level (not job-type-level) decisions; `ExecutionBroker` remains final
authority either way (PRD-011, WU-104) -- this is still just a pre-filter.
"""

from __future__ import annotations

from typing import Any

from wth_ai.jobs.model import JOB_TYPES
from wth_ai.models import PermissionMode
from wth_ai.permission_manager import PERMISSION_LEVEL

# Job types that operate against a target and therefore require one.
_TARGET_REQUIRED: frozenset[str] = frozenset({"recon", "executor"})

# Job types that run active attack/exploit modules against a target
# (handler_executor -> run_executor_job) rather than passive recon/reporting.
# Gated behind safe_mode per AUTHZ-05.
_DESTRUCTIVE_TYPES: frozenset[str] = frozenset({"executor"})

# WU-103: minimum PermissionMode floor per job type, mirroring the floor
# run_executor_job() already enforces post-admission (executor jobs require
# above READ_ONLY_RECON). Only checked when a caller actually supplies
# `permission_mode` -- omitted entirely (None) preserves pre-WU-103
# behaviour exactly for the 4 real callers that don't pass it yet.
_MIN_PERMISSION_LEVEL: dict[str, PermissionMode] = {
    "executor": PermissionMode.STANDARD_TESTING,
}


class PolicyEngine:
    def __init__(self, config: Any = None) -> None:
        self.config = config

    def evaluate(
        self,
        *,
        type: str,
        target: str | None = None,
        forbidden: tuple[str, ...] = (),
        safe_mode: bool = True,
        permission_mode: PermissionMode | str | None = None,
    ) -> tuple[bool, str]:
        """Return (allowed, reason). reason is empty when allowed.

        `permission_mode` (WU-103, optional): coarse pre-filter floor check,
        independent of `safe_mode` (ADR-012 -- distinct dimensions stay
        separately gated, neither bypasses the other). Omitted (None, the
        default) skips the check entirely -- unchanged behaviour for
        pre-WU-103 callers. An explicitly-supplied but unrecognized string
        DENIES rather than silently skipping (fail-closed: MISSING skips,
        INVALID denies).
        """
        if type not in JOB_TYPES:
            return False, f"unknown job type '{type}'"
        if type in _TARGET_REQUIRED and not (target and target.strip()):
            return False, f"job type '{type}' requires a target"
        if target:
            for pat in forbidden:
                if pat and pat.lower() in target.lower():
                    return False, f"target matches forbidden pattern '{pat}'"
        # Unrestricted mode: allow all job types including destructive.
        from wth_ai.engagement.mode import is_unrestricted
        if is_unrestricted(self.config if hasattr(self, "config") else None):
            return True, ""
        if safe_mode and type in _DESTRUCTIVE_TYPES:
            return False, (
                f"job type '{type}' is destructive; safe_mode requires "
                "unrestricted capability or an explicit safe_mode=False override"
            )
        floor = _MIN_PERMISSION_LEVEL.get(type)
        if floor is not None and permission_mode is not None:
            mode = permission_mode
            if isinstance(mode, str):
                mode = PermissionMode(mode) if mode in {p.value for p in PermissionMode} else None
            if mode is None:
                return False, f"job type '{type}': unrecognized permission_mode value"
            if PERMISSION_LEVEL[mode] < PERMISSION_LEVEL[floor]:
                return False, (
                    f"job type '{type}' requires permission mode >= '{floor.value}' "
                    f"(current: '{mode.value}')"
                )
        return True, ""
