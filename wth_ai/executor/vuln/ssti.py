"""SSTI executor — pipeline, ekspresi dipilih dari framework/konteks."""

from __future__ import annotations

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask

# Dipertahankan untuk import lama.
SSTI_PROBES: list[tuple[str, str, str]] = [
    ("{{7*7}}", "49", "jinja2/twindle"),
    ("{{7*'7'}}", "7777777", "jinja2"),
    ("${7*7}", "49", "freemarker/velocity"),
    ("#{7*7}", "49", "thymeleaf/ruby"),
    ("<%= 7*7 %>", "49", "erb/ejs"),
]


class SstiExecutor(BaseVulnExecutor):
    vuln_type = "ssti"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        from wth_ai.pipeline.executor import execute_pipeline

        return execute_pipeline(self, task, "ssti")
