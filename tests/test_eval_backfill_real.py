"""P1-3/P1-4: collect_class_counts/backfill_failures dengan store nyata.

Temuan audit 2026-11-06:
- P1-3: `collect_class_counts` memakai `learning.all_records()` yang TIDAK ada
  di `HuntMemoryLearning` — `rows` selalu kosong, `verified` selalu 0.
- P1-4: `backfill_failures` memanggil `learning.record_from_finding(finding,
  verified=False)` padahal signature `HuntMemoryLearning` butuh
  `(tech_stack, vuln_class, *, verified, ...)`. TypeError ditelan `except
  Exception: continue`, jadi `written` selalu 0.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")


def _learning(tmp_path) -> "HuntMemoryLearning":
    from wth_ai.memory.learning import HuntMemoryLearning

    return HuntMemoryLearning(db_path=tmp_path / "hunt_learning.db")


def _findings_db(false_positives: int = 1) -> "FindingsDB":
    from wth_ai.findings_db import FindingsDB

    db = FindingsDB()
    for i in range(false_positives):
        db.add_finding(
            {
                "title": f"false positive {i}",
                "severity": "medium",
                "state": "false_positive",
                "component": "api",
                "affected_asset": "/api/v1/orders",
                "cwe": "idors",
            }
        )
    return db


def test_hunt_memory_has_all_records(tmp_path):
    learning = _learning(tmp_path)
    from wth_ai.memory.learning import LearningRecord

    learning.record(LearningRecord(
        tech_stack="laravel", vuln_class="idor", success=True, tools_used=[]
    ))
    learning.record(LearningRecord(
        tech_stack="laravel", vuln_class="idor", success=False, tools_used=[]
    ))
    rows = learning.all_records()
    assert len(rows) == 2, rows
    by_success = sorted(int(r["success"]) for r in rows)
    assert by_success == [0, 1]


def test_collect_class_counts_reads_real_learning_store(tmp_path):
    from wth_ai.eval.act import collect_class_counts

    learning = _learning(tmp_path)
    from wth_ai.memory.learning import LearningRecord

    learning.record(LearningRecord(
        tech_stack="laravel", vuln_class="idor", success=True, tools_used=[]
    ))
    learning.record(LearningRecord(
        tech_stack="laravel", vuln_class="idor", success=True, tools_used=[]
    ))
    learning.record(LearningRecord(
        tech_stack="laravel", vuln_class="idor", success=False, tools_used=[]
    ))
    counts = collect_class_counts(learning)
    assert counts.get("idor", {}).get("verified") == 2, counts
    assert counts.get("idor", {}).get("false_positive") == 1, counts


def test_backfill_writes_false_positives_into_real_learning_store(tmp_path):
    from wth_ai.eval.act import backfill_failures

    learning = _learning(tmp_path)
    db = _findings_db(false_positives=2)
    action = backfill_failures(learning, db)
    assert action.applied, action.detail

    from wth_ai.eval.act import collect_class_counts

    counts = collect_class_counts(learning)
    assert counts.get("idors", {}).get("false_positive") == 2, counts
