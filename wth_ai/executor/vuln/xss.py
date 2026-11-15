"""XSS executor — pipeline sintesis konteks pantulan.

Marker/breakout dipilih dari konteks respons (html/attribute/js/encoded) oleh
`hunt/synthesis.py`; kelas ini hanya adapter.
"""

from __future__ import annotations

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask

# Dipertahankan untuk import lama.
MARKER = "wthXSSprobe9z8y7"


class XssExecutor(BaseVulnExecutor):
    vuln_type = "xss"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        from wth_ai.pipeline.executor import execute_pipeline

        return execute_pipeline(self, task, "xss")
