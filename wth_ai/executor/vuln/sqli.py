"""SQLi executor — sekarang pipeline (surface → baseline → probe → verdict).

Payload tidak lagi hard-coded di kelas ini: dialek/error/time dipilih
`hunt/synthesis.py`, kontrol boolean true/false disusun `SqliTechnique`, dan
verdict tetap milik `verify/deterministic.py`.
"""

from __future__ import annotations

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask

# Dipertahankan untuk kompatibilitas import lama; bukan sumber payload.
SQL_ERROR_MARKERS = (
    "you have an error in your sql syntax",
    "unclosed quotation mark",
    "quoted string not properly terminated",
    "unterminated quoted string",
    "postgresql",
    "sqlite3",
    "syntax error at or near",
    "sql syntax",
)


class SqliExecutor(BaseVulnExecutor):
    vuln_type = "sqli"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        from wth_ai.pipeline.executor import execute_pipeline

        return execute_pipeline(self, task, "sqli")
