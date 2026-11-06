"""Measurement harness — is the self-improvement actually improving?

Why this exists
---------------
`HuntMemoryLearning` has recorded outcomes since it was written, and NOTHING ever
measured them. A self-improving system without measurement cannot distinguish
improvement from drift: prompts and rules change, numbers move, and no one knows
which direction. Two facts found while building this harness, both of which it
now reports as first-class warnings:

1. **The learning store is structurally one-sided.** `record_from_finding` is
   called from exactly ONE place in the codebase (`orchestrator.py`, the
   `verified` branch). False positives only update `FindingsDB`. So
   `success_rate()` can never be dragged down by a failure -- it is not a
   precision, it is a tally of wins.
2. **The stores can silently desync.** `FindingsDB` holds verified AND
   false-positive findings; the learning DB holds only successes. Comparing the
   two is the cheapest way to notice that one of them stopped being written.

Metrics are computed offline from stores that already exist -- no LLM call, no
network -- so the harness is cheap enough to run after every engagement.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.workspace import atomic_write_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TechRow:
    tech_stack: str
    records: int
    lopsided: bool          # all records are successes -> rate is not a precision
    has_guidance: bool      # build_planner_context() actually returns text


@dataclass
class EvalReport:
    generated_at: str = field(default_factory=_now)
    metrics: dict[str, float] = field(default_factory=dict)
    per_tech: list[TechRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "metrics": self.metrics,
            "per_tech": [asdict(row) for row in self.per_tech],
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvalReport:
        rows = [TechRow(**row) for row in (data.get("per_tech") or []) if isinstance(row, dict)]
        return cls(
            generated_at=str(data.get("generated_at") or ""),
            metrics={k: float(v) for k, v in (data.get("metrics") or {}).items()},
            per_tech=rows,
            warnings=list(data.get("warnings") or []),
        )


def _learning_stats(learning: Any) -> tuple[dict[str, float], list[TechRow], list[str]]:
    """Read the learning store. Returns metrics, per-tech rows, warnings."""
    warnings: list[str] = []
    metrics: dict[str, float] = {}
    rows: list[TechRow] = []

    db_path = Path(getattr(learning, "db_path", Path("/nonexistent")))
    if not db_path.exists():
        warnings.append(f"learning store belum ada: {db_path}")
        return metrics, rows, warnings

    try:
        with sqlite3.connect(db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
            failures = conn.execute("SELECT COUNT(*) FROM outcomes WHERE success = 0").fetchone()[0]
            stacks = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT tech_stack FROM outcomes ORDER BY tech_stack"
                ).fetchall()
            ]
            per_stack = {
                row[0]: (row[1], row[2])
                for row in conn.execute(
                    "SELECT tech_stack, COUNT(*), SUM(success) FROM outcomes GROUP BY tech_stack"
                ).fetchall()
            }
    except sqlite3.Error as exc:
        warnings.append(f"learning store tidak bisa dibaca: {exc}")
        return metrics, rows, warnings

    metrics["learning_records"] = float(total)
    metrics["learning_failures"] = float(failures)
    metrics["learning_tech_stacks"] = float(len(stacks))

    guidance_count = 0
    for stack in stacks:
        records, successes = per_stack.get(stack, (0, 0))
        try:
            has_guidance = bool(learning.build_planner_context(stack))
        except Exception:  # noqa: BLE001 - a broken store must not kill the report
            has_guidance = False
        if has_guidance:
            guidance_count += 1
        rows.append(
            TechRow(
                tech_stack=stack,
                records=int(records or 0),
                lopsided=bool(records) and int(successes or 0) == int(records),
                has_guidance=has_guidance,
            )
        )
    metrics["learning_guidance_reachable"] = float(guidance_count)
    # Now that BOTH verdicts are recorded, the store can express a real
    # precision. Historical rows written before that fix remain one-sided, which
    # `collect()` cross-checks against FindingsDB.
    decided = total
    if decided:
        metrics["learning_precision"] = round((total - failures) / decided, 3)

    if not total:
        warnings.append(
            "learning store KOSONG: belum ada satu pun outcome tercatat, jadi klaim "
            "'self-improvement' belum punya data untuk dibuktikan."
        )
    if total and guidance_count < len(stacks):
        metrics["learning_guidance_gap"] = float(len(stacks) - guidance_count)
        warnings.append(
            f"{len(stacks) - guidance_count} tech stack punya riwayat tapi TIDAK menghasilkan "
            "guidance saat build_planner_context() dipanggil — riwayatnya tersimpan tapi tidak "
            "terjangkau di waktu planning."
        )
    return metrics, rows, warnings


def _findings_stats(db: Any) -> tuple[dict[str, float], list[str]]:
    """Real precision, from the store that DOES keep both outcomes."""
    warnings: list[str] = []
    metrics: dict[str, float] = {}
    try:
        verified = len(db.list_findings("verified"))
        false_positive = len(db.list_findings("false_positive"))
        total = len(db.list_findings())
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"FindingsDB tidak bisa dibaca: {type(exc).__name__}: {exc}")
        return metrics, warnings

    metrics["findings_total"] = float(total)
    metrics["findings_verified"] = float(verified)
    metrics["findings_false_positive"] = float(false_positive)
    decided = verified + false_positive
    metrics["findings_precision"] = round(verified / decided, 3) if decided else 0.0
    if decided == 0:
        warnings.append(
            "belum ada temuan yang diputuskan (verified/false_positive), jadi presisi "
            "belum terukur."
        )
    return metrics, warnings


def collect(*, learning: Any = None, findings_db: Any = None) -> EvalReport:
    """Build one report from the live stores."""
    report = EvalReport()
    if learning is None:
        from wth_ai.memory.learning import HuntMemoryLearning

        learning = HuntMemoryLearning()
    if findings_db is None:
        from wth_ai.findings_db import FindingsDB

        findings_db = FindingsDB()

    learn_metrics, rows, learn_warnings = _learning_stats(learning)
    findings_metrics, findings_warnings = _findings_stats(findings_db)

    report.metrics.update(learn_metrics)
    report.metrics.update(findings_metrics)
    report.per_tech = rows
    report.warnings = learn_warnings + findings_warnings

    # LLM spend, so a report answers "what did this cost?" next to "did it work?".
    # A provider that reports no usage still shows up as calls -- "not measured"
    # is the honest signal, and omitting it would understate spend.
    try:
        from wth_ai.llm.usage import get_ledger

        usage = get_ledger().summary()
        report.metrics["llm_calls"] = float(usage["calls"])
        report.metrics["llm_tokens"] = float(usage["total_tokens"])
        if usage["calls"] and not usage["total_tokens"]:
            report.warnings.append(
                f"{int(usage['calls'])} call tercatat TAPI 0 token: providernya tidak "
                "melaporkan usage, jadi biaya engagement ini belum terukur."
            )
        if usage.get("over_budget"):
            report.warnings.append(
                f"pemakaian token melewati budget: {int(usage['total_tokens'])} > "
                f"{int(usage['budget_tokens'])} (WTH_TOKEN_BUDGET)."
            )
    except Exception:  # noqa: BLE001
        pass

    # Asymmetry diagnosis, cross-checked between the two stores. A learning store
    # with zero failures is either (a) genuinely FP-free, or (b) written before
    # both verdicts were recorded -- and (b) is far more likely when FindingsDB is
    # holding false positives at the same time.
    total_learned = report.metrics.get("learning_records", 0.0)
    failures = report.metrics.get("learning_failures", 0.0)
    findings_fp = report.metrics.get("findings_false_positive", 0.0)
    if total_learned and not failures:
        if findings_fp:
            report.warnings.append(
                f"learning store BERPHAK SATU: {int(total_learned)} outcome, 0 kegagalan, "
                f"padahal FindingsDB menyimpan {int(findings_fp)} false positive — record ini "
                "ditulis sebelum pencatatan dua arah diperbaiki, jadi persentasenya belum "
                "mencerminkan presisi."
            )
        else:
            report.warnings.append(
                f"learning store BERPHAK SATU: {int(total_learned)} outcome, 0 kegagalan. "
                "Bisa berarti memang belum ada false positive, atau pencatatannya belum jalan — "
                "bedakan dengan menambah engagement, jangan diasumsikan."
            )

    # The desync check: the two stores should agree on how many verified findings
    # exist. A gap means one of them stopped being written.
    learned = report.metrics.get("learning_records", 0.0)
    verified = report.metrics.get("findings_verified", 0.0)
    if learned or verified:
        report.metrics["store_sync_gap"] = abs(learned - verified)
        if learned != verified:
            report.warnings.append(
                f"learning mencatat {int(learned)} outcome tapi FindingsDB punya "
                f"{int(verified)} temuan verified — dua store ini tidak sinkron "
                "(bukti bahwa salah satunya berhenti ditulis, bukan bahwa salah satunya benar)."
            )
    return report


# --------------------------------------------------------------------------- #
# persistence + comparison
# --------------------------------------------------------------------------- #
def eval_dir(root: Path | None = None) -> Path:
    if root is not None:
        return root
    try:
        from wth_ai.workspace import wth_home

        return wth_home() / "eval"
    except Exception:  # noqa: BLE001
        return Path.home() / ".wth-ai" / "eval"


def save(report: EvalReport, root: Path | None = None) -> Path:
    directory = eval_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.replace(":", "").replace("-", "")[:15]
    path = directory / f"eval-{stamp}.json"
    atomic_write_text(path, json.dumps(report.to_dict(), indent=2) + "\n")
    return path


def load_latest(root: Path | None = None, *, exclude: str = "") -> EvalReport | None:
    directory = eval_dir(root)
    if not directory.exists():
        return None
    files = sorted(directory.glob("eval-*.json"))
    for path in reversed(files):
        if exclude and path.name == exclude:
            continue
        try:
            return EvalReport.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return None


def compare(new: EvalReport, old: EvalReport | None) -> str:
    """Answer 'better than last time?' in numbers, or say there is nothing to compare."""
    if old is None:
        return "Belum ada laporan sebelumnya — ini baseline pertama."
    lines = [f"Perbandingan dengan {old.generated_at}:"]
    keys = sorted(set(new.metrics) | set(old.metrics))
    moved = 0
    for key in keys:
        before = old.metrics.get(key)
        after = new.metrics.get(key)
        if before is None or after is None:
            lines.append(f"  {key}: {'-' if before is None else before} -> {'-' if after is None else after} (baru/hilang)")
            continue
        delta = after - before
        if abs(delta) < 1e-9:
            continue
        moved += 1
        arrow = "↑" if delta > 0 else "↓"
        lines.append(f"  {key}: {before} -> {after} ({arrow}{abs(round(delta, 3))})")
    if not moved:
        lines.append("  tidak ada metrik yang berubah.")
    return "\n".join(lines)


def format_report(report: EvalReport, *, comparison: str = "") -> str:
    lines = [f"Eval report — {report.generated_at}"]
    if report.metrics:
        lines.append("Metrik:")
        for key, value in sorted(report.metrics.items()):
            lines.append(f"  {key}: {value:g}")
    else:
        lines.append("Metrik: (tidak ada — store belum berisi data)")
    if report.per_tech:
        lines.append("Per tech stack:")
        for row in report.per_tech:
            flags = []
            if row.lopsided:
                flags.append("lopsided")
            if not row.has_guidance:
                flags.append("tanpa-guidance")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            lines.append(f"  {row.tech_stack}: {row.records} record{suffix}")
    if report.warnings:
        lines.append("Peringatan (jangan diabaikan):")
        for warning in report.warnings:
            lines.append(f"  ⚠ {warning}")
    if comparison:
        lines.append("")
        lines.append(comparison)
    return "\n".join(lines)


def run(*, learning: Any = None, findings_db: Any = None, root: Path | None = None) -> tuple[EvalReport, Path]:
    """Collect, persist and return (report, path)."""
    report = collect(learning=learning, findings_db=findings_db)
    path = save(report, root)
    return report, path
