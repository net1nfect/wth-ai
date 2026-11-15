"""Command injection executor — pipeline, separator/OS dipilih dari respons."""

from __future__ import annotations

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask


class CmdiExecutor(BaseVulnExecutor):
    vuln_type = "cmdi"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        from wth_ai.pipeline.executor import execute_pipeline

        return execute_pipeline(self, task, "cmdi")
