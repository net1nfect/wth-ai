"""Adapter executor lama → pipeline probe.

Kelas executor tetap ada karena kontrak `VULN_EXECUTORS`/arena memakainya,
tetapi isinya sekarang hanya memanggil pipeline. HTTP, baseline, sintesis, dan
verifikasi tidak lagi ditulis ulang di tiap kelas.
"""

from __future__ import annotations

from typing import Any

from wth_ai.models import ExecutorResult, ExecutorTask
from wth_ai.pipeline.discovery import (
    discover_document,
    discover_from_js,
    external_script_urls,
    js_import_urls,
)
from wth_ai.pipeline.models import RequestSpec, Surface
from wth_ai.pipeline.session import ProbeSession, scope_guard_from
from wth_ai.pipeline.runner import run_technique
from wth_ai.pipeline.techniques import get_technique


def execute_pipeline(executor: Any, task: ExecutorTask, vuln_class: str) -> ExecutorResult:
    surface = Surface.from_target(
        task.target, method=getattr(task, "method", "GET") or "GET",
        headers=getattr(task, "headers", None), body=getattr(task, "body", "") or "",
    )
    technique = get_technique(vuln_class)
    if technique is None:
        return executor._result(
            task, status="failed",
            summary=f"Tidak ada technique pipeline untuk {vuln_class}",
            evidence=[], observations=[], risk="low",
        )
    state = getattr(executor, "state", None)
    scope = getattr(state, "scope", None)
    rate = float(getattr(scope, "rate_limit_rps", 0.0) or 0.0)
    session = ProbeSession(rate_limit_rps=rate, scope_guard=scope_guard_from(scope))
    context = {
        "task": task,
        "state": state,
        "metadata": dict(getattr(state, "metadata", {}) or {}),
        "executor": executor,
    }

    discovered = None
    if not surface.params and getattr(technique, "requires_parameter", True):
        # Target bisa berupa halaman, bukan endpoint berparameter. Ambil satu
        # halaman lalu ekstrak form/link/JS; bila ada surface berparameter,
        # pipeline menguji endpoint yang benar-benar dipakai halaman itu.
        page, page_html = session.baseline(surface)
        if page.status == 0:
            # Target tanpa parameter yang tidak bisa difetch tetap "tidak ada
            # yang diprobe", bukan kegagalan kemampuan: executor tidak punya
            # endpoint untuk diuji. Jangan mengarang temuan.
            return executor._result(
                task, status="success",
                summary=f"Tidak ada parameter (query/JSON/form) untuk probe {vuln_class}",
                evidence=[], observations=[
                    f"target={surface.url}",
                    f"seed_status=0 ({page.snippet or 'request gagal'})",
                ], risk="low",
            )
        candidates = [
            item for item in discover_document(
                page.url or surface.url, page_html,
                content_type=str(page.headers.get("content-type", "")),
            )
            if item.surface.params
        ]
        if not candidates:
            # Banyak halaman modern menaruh route API di file JS same-origin,
            # bukan inline. Fetch terbatas (<=3) sebelum menyimpulkan tidak ada
            # surface; semua lewat ProbeSession yang sama.
            script_seed = page.url or surface.url
            queue = list(external_script_urls(script_seed, page_html, max_urls=3))
            seen_scripts: set[str] = set()
            while queue and len(seen_scripts) < 5:
                script_url = queue.pop(0)
                if script_url in seen_scripts:
                    continue
                seen_scripts.add(script_url)
                script_obs, script_body = session.send(
                    RequestSpec("GET", script_url), role="discover:js",
                )
                if script_obs.status != 200 or len(script_body) > 512_000:
                    continue
                candidates.extend(
                    item for item in discover_from_js(script_url, script_body)
                    if item.surface.params
                )
                for imported in js_import_urls(script_url, script_body, max_urls=2):
                    if imported not in seen_scripts:
                        queue.append(imported)
        if not candidates:
            return executor._result(
                task, status="success",
                summary=f"Tidak ada parameter (query/JSON/form) untuk probe {vuln_class}",
                evidence=[], observations=[
                    f"target={surface.url}",
                    f"seed_status={page.status}",
                    "surface discovery: 0 kandidat berparameter",
                ], risk="low",
            )
        discovered = candidates[0]
        surface = discovered.surface
        context["discovered"] = discovered.to_dict()

    run = run_technique(technique, surface, session, context=context)
    baseline = run.baseline
    baseline_body = run.baseline_body
    if baseline.status == 0:
        return executor._result(
            task, status="failed", summary=baseline.snippet or "baseline request gagal",
            evidence=[], observations=[], risk="low",
        )

    result = run.result
    pairs = run.pairs
    observations = [
        f"parameter={','.join(p.name for p in surface.params) or '-'}",
        f"baseline={baseline.status}/len={baseline.body_length}",
        f"probes={len(run.outcomes)}",
        f"skipped={run.skipped}",
    ]
    if discovered is not None:
        observations.append(f"discovered={discovered.source}:{discovered.surface.method} {discovered.surface.url}")
    observations.extend(result.observations[:8])
    label = getattr(technique, "label", "") or f"Pipeline {vuln_class}"
    summary = (
        f"{label} — {'; '.join(result.evidence) if result.evidence else 'belum ada sinyal'}"
    )
    return executor._result(
        task,
        status="success",
        summary=summary[:1200],
        evidence=result.evidence,
        observations=observations,
        risk=result.risk,
        request_pairs=pairs,
    )
