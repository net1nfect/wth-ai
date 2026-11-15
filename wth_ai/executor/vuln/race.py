"""Race condition executor — sekarang pipeline burst (surface → baseline → 20x paralel)."""

from __future__ import annotations

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask


class RaceExecutor(BaseVulnExecutor):
    vuln_type = "race"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        from wth_ai.pipeline.executor import execute_pipeline

        return execute_pipeline(self, task, "race")
