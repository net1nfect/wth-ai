"""WTH-AI Test Planning Layer.

Bridges the cognition engine (risk-ranked hypotheses) to the executor by
producing a module-bound, permission-gated, budgeted, non-destructive test
queue.

  - modules      : vulnerability module taxonomy (executor modules 3-12)
  - test_planner : TestPlanner -> TestPlan (queue of PlannedTest / ExecutorTask)

Safety: planning only. Nothing here sends requests, escalates permissions, or
bypasses scope; the executor + PermissionManager remain the sole actors.
"""

from __future__ import annotations

from wth_ai.planning.modules import (
    MODULE_BY_ID,
    MODULES,
    TestFamily,
    VulnModule,
    module_for_vuln_class,
)
from wth_ai.planning.test_planner import PlannedTest, TestPlan, TestPlanner

__all__ = [
    "MODULES",
    "MODULE_BY_ID",
    "VulnModule",
    "TestFamily",
    "module_for_vuln_class",
    "TestPlanner",
    "TestPlan",
    "PlannedTest",
]
