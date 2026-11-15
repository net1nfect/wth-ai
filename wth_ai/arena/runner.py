"""Arena registry + runner — uji kemampuan nyata, bukan klaim.

Kasus = (executor, endpoint rentan, bukti yang WAJIB muncul di evidence).
PASS  : ≥1 evidence mengandung `expect`.
SKIP  : binary `requires` tidak tersedia (jujur: bukan gagal).
FAIL  : executor jalan tapi bukti tidak muncul — regresi kemampuan.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable

from wth_ai.arena.targets import ArenaServer, SELFTEST_MARKER
from wth_ai.models import SessionState

TINY_JWT_ALG_NONE = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiIxIiwiZXhwIjoxOTAwMDAwMDAwfQ."


@dataclass
class ArenaCase:
    case_id: str
    vuln_type: str
    path: str
    expect: str
    #: persyaratan binary; kosong = built-in executor tanpa dependensi.
    requires: str = ""
    #: mutasi state per kasus (mis. captured_jwt untuk auth)
    setup_state: Callable[[SessionState], None] | None = None
    notes: str = ""


#: Kumpulan kasus — kelas yang TELAH ada executornya. Nol klaim: setiap kasus
#: di sini harus ditemukan benuar oleh executor yang sudah terverifikasi.
ARENA_CASES: list[ArenaCase] = [
    ArenaCase("sqli_error", "sqli", "/sqli?id=7",
              expect="error in your sql syntax"),
    ArenaCase("sqli_boolean", "sqli", "/sqli?id=7",
              expect="boolean-blind",
              notes="papan boolean 1=1 vs 1=2 harus membedakan respons"),
    ArenaCase("ssti_evaluate", "ssti", "/ssti?name=x",
              expect="server-side"),
    ArenaCase("cmdi_uid", "cmdi", "/cmdi?host=localhost",
              expect="uid="),
    ArenaCase("traversal_passwd", "path_traversal", "/trav?file=notes.txt",
              expect="root:x:0:0:"),
    ArenaCase("xss_unescaped", "xss", "/xss?q=a",
              expect="unescaped"),
    ArenaCase("ssrf_content_proof", "ssrf", "/ssrf?url=x",
              expect="TERBUKTI: isi halaman internal"),
    ArenaCase("idor_object_swap", "idor", "/api/user/2",
              expect="dapat dibaca tanpa otorisasi"),
    ArenaCase("auth_jwt_alg_none", "auth_jwt", "/api/me",
              expect="forged token accepted",
              setup_state=lambda st: st.metadata.update(captured_jwt=TINY_JWT_ALG_NONE)),
    ArenaCase("race_burst", "race", "/redeem",
              expect="paralel mengembalikan 2xx"),
    ArenaCase("nuclei_selftest", "nuclei", "/",
              expect=f"nuclei[HIGH]",
              requires="nuclei",
              notes="template arena lokal, hasil deterministik; skip bila binary absen"),
    # controller: marker SELFTEST_MARKER dipakai template yaml di arena/templates.
]


@dataclass
class ArenaCaseResult:
    case_id: str
    outcome: str  # PASS | SKIP | FAIL
    detail: str
    evidence: list[str] = field(default_factory=list)
    elapsed: float = 0.0


@dataclass
class ArenaReport:
    results: list[ArenaCaseResult]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.outcome == "PASS")

    @property
    def failed(self) -> list[ArenaCaseResult]:
        return [r for r in self.results if r.outcome == "FAIL"]

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.outcome == "SKIP")

    def format(self) -> str:
        lines = ["ARENA RUN — uji kemampuan nyata (bukan klaim)"]
        for r in self.results:
            tick = {"PASS": "✓", "FAIL": "✗", "SKIP": "–"}[r.outcome]
            lines.append(f"{tick} {r.case_id:<20} {r.outcome:<5} {r.detail[:80]}")
        total = len(self.results)
        lines.append(
            f"\n{self.passed}/{total} kasus terdeteksi · {self.skipped} skip "
            f"· {len(self.failed)} fail"
        )
        if self.failed:
            lines.append("REGRESI: kemampuan turun pada kasus di atas.")
        return "\n".join(lines)


def analyze_nuclei_case(case: ArenaCase, base: str) -> ArenaCaseResult:
    """nuclei memerlukan template lokal deterministik (bukan template global)."""
    from pathlib import Path

    from wth_ai.executor.vuln.nuclei import NucleiExecutor, nuclei_run

    tpl_dir = Path(__file__).parent / "templates"
    started = time.perf_counter()
    state = SessionState()
    ex = NucleiExecutor(state)

    def with_templates(target: str):  # type: ignore[no-untyped-def]
        return nuclei_run(target, extra=["-t", str(tpl_dir)])

    ex._run_nuclei = with_templates  # type: ignore[method-assign]
    from wth_ai.models import ExecutorTask, PermissionMode

    task = ExecutorTask(id=f"arena-{case.case_id}", goal="arena",
                        target=base + case.path,
                        permission_required=PermissionMode.STANDARD_TESTING)
    r = ex.execute(task)
    elapsed = time.perf_counter() - started
    el = case.expect.lower()
    hit = any(el in e.lower() for e in r.evidence)
    detail = f"{r.output_summary[:80]}"
    return ArenaCaseResult(case.case_id, "PASS" if hit else "FAIL",
                           detail, list(r.evidence), elapsed)


def run_arena(cases: list[ArenaCase] | None = None) -> ArenaReport:
    """Jalankan arena penuh: server vuln lokal + setiap executor yang terdaftar."""
    from wth_ai.executor.vuln import VULN_EXECUTORS
    from wth_ai.models import ExecutorTask, PermissionMode

    cases = cases if cases is not None else ARENA_CASES
    server = ArenaServer()
    results: list[ArenaCaseResult] = []
    try:
        base = server.base
        for case in cases:
            started = time.perf_counter()
            if case.requires and shutil.which(case.requires) is None:
                results.append(ArenaCaseResult(
                    case.case_id, "SKIP", f"binary '{case.requires}' tidak tersedia"))
                continue

            if case.vuln_type == "nuclei":
                results.append(analyze_nuclei_case(case, base))
                continue

            state = SessionState()
            state.scope.target = "127.0.0.1"
            state.scope.authorized = True
            state.scope.rate_limit_rps = 100.0
            if case.setup_state:
                case.setup_state(state)

            executor_cls = VULN_EXECUTORS.get(case.vuln_type)
            if executor_cls is None:
                results.append(ArenaCaseResult(
                    case.case_id, "FAIL", f"executor '{case.vuln_type}' tidak terdaftar"))
                continue

            task = ExecutorTask(id=f"arena-{case.case_id}", goal="arena",
                                target=base + case.path,
                                permission_required=PermissionMode.STANDARD_TESTING)
            try:
                r = executor_cls(state).execute(task)
            except Exception as exc:  # noqa: BLE001 — crash executor = FAIL, bukan raise++
                results.append(ArenaCaseResult(
                    case.case_id, "FAIL", f"executor crash: {exc}", [], time.perf_counter() - started))
                continue

            el = case.expect.lower()
            hit = any(el in e.lower() for e in r.evidence)
            results.append(ArenaCaseResult(
                case.case_id,
                "PASS" if hit else "FAIL",
                r.output_summary[:100],
                list(r.evidence),
                time.perf_counter() - started,
            ))
    finally:
        server.stop()

    report = ArenaReport(results)
    _persist(report)
    return report


def _persist(report: ArenaReport) -> None:
    """Simpan JSON terakhir di WTH_HOME/arena — bukti yang bisa dibanding antar-run."""
    try:
        from wth_ai.workspace import wth_home

        out = wth_home() / "arena"
        out.mkdir(parents=True, exist_ok=True)
        payload = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "passed": report.passed,
            "failed": [r.case_id for r in report.failed],
            "skipped": report.skipped,
            "cases": [
                {"case_id": r.case_id, "outcome": r.outcome, "detail": r.detail,
                 "evidence": r.evidence[:6], "elapsed": round(r.elapsed, 3)}
                for r in report.results
            ],
        }
        (out / "last_run.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001 — persist boleh gagal, run tetap valid
        pass
