"""Default job handlers.

Handler signature: ``handler(job, ctx) -> dict``. ``ctx`` exposes
``ctx.log(str)``, ``ctx.progress(current, total, message)`` and
``ctx.should_stop()``. Fase 5 wires the real recon platform behind
``handler_recon``; executor/report use orchestrator bridge.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

Handler = Callable[[Any, Any], dict[str, Any]]


def _load_baseline(store: Any, target: str, current_job_id: str) -> dict[str, Any] | None:
    """Return the normalized result of the most recent prior recon for ``target``."""
    if store is None or not target:
        return None
    try:
        prior = store.list_jobs(type="recon", status="completed", limit=50)
    except Exception:  # noqa: BLE001
        return None
    for j in prior:
        if j.job_id == current_job_id or (j.target or "") != target:
            continue
        path = Path(store.results_dir) / j.job_id / "normalized_result.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return None


def _load_fingerprint_tech_stack(output_dir: Any) -> dict[str, str]:
    """Technology -> version map for a recon job, from its normalized artifacts.

    The job summary only carries COUNTS, so the previous
    ``summary.get("fingerprints", {})`` was wrong twice over: the real records
    live under ``summary["assets"]`` (built by ``ReconNormalizer.summary()``),
    and even there ``fingerprints`` is an int, not a map of technologies. The
    actual records are written to ``normalized/fingerprint.json``.
    """
    try:
        path = Path(str(output_dir)) / "normalized" / "fingerprint.json"
        if not path.exists():
            return {}
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        tech = str(row.get("technology") or "").strip()
        if tech:
            out.setdefault(tech, str(row.get("version") or ""))
    return out


def _sync_graph_after_recon(
    result: dict[str, Any],
    job: Any,
    ctx: Any,
    *,
    target: str,
) -> dict[str, Any] | None:
    """Populate session AssetGraph from recon job output."""
    output_dir = result.get("output_dir")
    if not output_dir:
        return None
    meta = getattr(job, "metadata", {}) or {}
    session_id = meta.get("session_id")
    graph_sync: dict[str, Any] | None = None
    try:
        from wth_ai.graph.asset_graph import AssetGraph
        from wth_ai.graph.recon_bridge import sync_recon_to_graph
        from wth_ai.session_store import SessionStore
        from wth_ai.config import WTHConfig

        if session_id:
            graph = AssetGraph(session_id)
            graph_sync = sync_recon_to_graph(output_dir, graph, target=target)
            store = SessionStore(WTHConfig.load())
            state = store.load(session_id)
            if state:
                state.metadata["last_recon"] = {
                    "job_id": job.job_id,
                    "target": target,
                    "graph_nodes": graph_sync.get("nodes", 0) if graph_sync else 0,
                }
                tech_stack = _load_fingerprint_tech_stack(output_dir)
                if tech_stack:
                    state.metadata["attack_surface_obj"] = {
                        "target": target,
                        "tech_stack": tech_stack,
                    }
                store.save(state)
        manager = getattr(ctx, "_manager", None)
        bus = getattr(manager, "bus", None)
        if bus:
            bus.emit(
                "recon.completed",
                job_id=job.job_id,
                data={"target": target, "output_dir": output_dir, "graph_sync": graph_sync or {}},
            )
    except Exception as exc:  # noqa: BLE001
        ctx.log(f"graph sync warning: {exc}")
    return graph_sync


def handler_recon(job: Any, ctx: Any) -> dict[str, Any]:
    """Run the real recon engine (authorized, recon-only, non-destructive)."""
    from wth_ai.recon.engine import ReconEngine

    target = job.target or ""
    if not target:
        return {"type": "recon", "status": "failed", "error": "no target"}
    if ctx.should_stop():
        return {"type": "recon", "status": "stopped"}

    meta = getattr(job, "metadata", {}) or {}
    profile = job.profile or meta.get("profile") or "recon-only"
    # Fail-closed, matching `orchestrator_bridge.run_executor_job()`'s
    # `metadata.get("authorized", False)`. This used to default to True, which
    # meant ANY caller that forgot to pass the flag got a live scan against an
    # unconfirmed target -- and the authorization gate in `ReconEngine.run()`
    # could never fire, because nothing ever sent authorized=False. Callers that
    # represent explicit operator intent (`wth recon run/quick`, the `/recon`
    # slash handler, a `recon watch` cron entry, the supervisor's
    # `job_create_recon`) set it explicitly at that boundary.
    from wth_ai.engagement.mode import is_no_limits

    authorized = is_no_limits() or bool(meta.get("authorized", False))
    realtime = bool(meta.get("realtime", False))

    manager = getattr(ctx, "_manager", None)
    bus = getattr(manager, "bus", None)
    store = getattr(manager, "store", None)
    baseline = _load_baseline(store, target, job.job_id)

    ctx.log(f"recon start target={target} profile={profile} authorized={authorized}")
    engine = ReconEngine()

    # Checkpoint per-tool: crash di tengah scan tidak membuang tool yang sudah
    # selesai, dan retry tidak menghubungi ulang tool tersebut. Store adalah
    # satu-satunya sumber kebenaran; tanpa store, scan tetap jalan tapi
    # recovery-nya dilaporkan tidak tersedia (bukan diklaim ada).
    resume_cp = None
    if store is not None:
        raw = store.load_checkpoint(job.job_id)
        if raw:
            try:
                from wth_ai.jobs.checkpoint import CheckpointEnvelope

                resume_cp = CheckpointEnvelope.from_dict(raw)
                ctx.log(
                    f"recovery: checkpoint recon dimuat "
                    f"({len(resume_cp.engine_state.get('tools') or {})} tool tercatat)"
                )
            except Exception as exc:  # noqa: BLE001 - korup/asing => mulai bersih
                ctx.log(f"recovery: checkpoint recon ditolak ({type(exc).__name__}; mulai dari awal)")
                resume_cp = None

    def _save_recon_cp(env: Any) -> None:
        if store is None:
            return
        store.save_checkpoint(job.job_id, env.to_dict())

    result = engine.run(
        target,
        profile=profile,
        job_id=job.job_id,
        authorized=authorized,
        realtime=realtime,
        progress=ctx.progress,
        log=ctx.log,
        bus=bus,
        baseline=baseline,
        # `wth job stop` can now actually stop a running scan: the engine polls
        # this between tools instead of the handler checking once at entry.
        should_stop=ctx.should_stop,
        checkpoint=_save_recon_cp if store is not None else None,
        resume=resume_cp,
    )

    if realtime:
        try:
            from wth_ai.recon.realtime import record_run

            record_run(target, job.job_id, profile=profile)
        except Exception:  # noqa: BLE001
            pass

    graph_sync = _sync_graph_after_recon(result, job, ctx, target=target)

    return {
        "type": "recon",
        "target": target,
        "status": result.get("status", "completed"),
        "profile": profile,
        # Propagate the engine's refusal reason so a blocked scan surfaces as a
        # readable job error instead of a bare "blocked".
        "reason": result.get("reason"),
        "output_dir": result.get("output_dir"),
        "summary": result.get("summary", {}),
        "priority_targets": result.get("priority_targets", 0),
        "changes": result.get("changes", {}),
        "graph_sync": graph_sync,
    }


def handler_executor(job: Any, ctx: Any) -> dict[str, Any]:
    from wth_ai.jobs.orchestrator_bridge import run_executor_job

    ctx.log("executor start")
    return run_executor_job(job, ctx)


def handler_report(job: Any, ctx: Any) -> dict[str, Any]:
    from wth_ai.jobs.orchestrator_bridge import run_report_job

    ctx.log("report start")
    return run_report_job(job, ctx)


def handler_provider_test(job: Any, ctx: Any) -> dict[str, Any]:
    from wth_ai.config import WTHConfig
    from wth_ai.llm.router import LLMProviderRouter

    provider = job.target or (getattr(job, "metadata", {}) or {}).get("provider", "openrouter")
    ctx.log(f"provider_test start provider={provider}")
    ctx.progress(10, 100, "testing provider")
    router = LLMProviderRouter(WTHConfig.load())
    result = router.test_provider(provider)
    ctx.progress(100, 100, "done")
    return {"type": "provider_test", "provider": provider, **result}


def handler_custom(job: Any, ctx: Any) -> dict[str, Any]:
    meta = getattr(job, "metadata", {}) or {}
    action = meta.get("action", "")
    if action == "echo":
        ctx.progress(100, 100, "done")
        return {"type": "custom", "status": "completed", "echo": meta}
    ctx.progress(100, 100, "done")
    return {
        "type": "custom",
        "status": "skipped",
        "note": "custom jobs require metadata.action",
        "metadata": meta,
    }


def handler_autopilot(job: Any, ctx: Any) -> dict[str, Any]:
    """Autopilot berkala: langkah uji BERBATAS, disusun lalu dijalankan lalu dipelajari.

    Gerbang yang dijaga:
    * `authorized` wajib eksplisit (fail-closed, sama seperti handler_recon);
    * opt-in destruktif DIBACA dari scope tersimpan saat job berjalan -- cron tidak
      pernah menjadi jalan pintas untuk membukanya;
    * metode pengubah data memakai aktor `executor` (butuh STANDARD_TESTING), metode
      baca memakai aktor `recon` -- sehingga sesi read-only tetap read-only.
    """
    from wth_ai.hunt.autopilot import format_report, run_autopilot

    target = job.target or ""
    if not target:
        return {"type": "autopilot", "status": "failed", "error": "no target"}
    from wth_ai.engagement.mode import is_no_limits

    no_limits = is_no_limits()
    meta = getattr(job, "metadata", {}) or {}
    if not no_limits and not bool(meta.get("authorized", False)):
        return {"type": "autopilot", "status": "blocked",
                "reason": "authorized flag required (fail-closed)"}
    if ctx.should_stop():
        return {"type": "autopilot", "status": "stopped"}

    ctx.progress(10, 100, "autopilot: menyusun langkah")
    try:
        from wth_ai.config import WTHConfig
        from wth_ai.models import SessionState
        from wth_ai.permission_manager import PermissionManager
        from wth_ai.scope_manager import ScopeManager
        from wth_ai.terminal.bridge import AgentTerminalBridge
    except Exception as exc:  # noqa: BLE001
        return {"type": "autopilot", "status": "failed", "error": f"import: {exc}"}

    config = WTHConfig.load()
    session_id = meta.get("session_id") or f"autopilot-{job.job_id}"

    # PermissionManager menerima MODE, bukan config. Versi pertama gua mengirim
    # `PermissionManager(config)`, sehingga `mode` berisi objek config dan
    # `mode_sufficient()` melempar "TypeError: unhashable type: 'WTHConfig'" -- SETIAP
    # probe gagal dan hasilnya tampil sebagai status kosong (terukur di UAT CLI:
    # langkah autopilot "dijalankan" tapi nol permintaan nyata sampai ke target).
    # Mode diambil dari sesi tersimpan supaya job berjalan dengan izin yang sama
    # seperti yang operator pakai, bukan default.
    permission = PermissionManager()
    saved_scope = None
    try:
        from wth_ai.session_store import SessionStore

        saved_scope = SessionStore(config).load_latest()
    except Exception:  # noqa: BLE001
        saved_scope = None
    if saved_scope is not None:
        mode_value = getattr(saved_scope, "permission_mode", None)
        if mode_value is not None:
            try:
                permission.set_mode(getattr(mode_value, "value", mode_value))
            except Exception:  # noqa: BLE001
                pass

    # Scope bridge diisi SEBELUM bridge dibuat (bukan dimutasi sesudahnya): bridge
    # memakai ScopeManager sendiri, dan tanpa target/in-scope di sana setiap probe
    # berhenti di gate scope dengan status kosong.
    scope_manager = ScopeManager()
    scope_manager.set_scope(target=target, authorized=True, in_scope=[target])
    bridge = AgentTerminalBridge(config, permission, scope_manager, session_id)

    state = SessionState()
    state.scope.target = target
    state.scope.in_scope = [target]
    state.scope.authorized = True
    # Opt-in destruktif dari scope yang TERSIMPAN (hasil `/scope set destructive=yes`),
    # bukan dari metadata job -- supaya cron tidak bisa membukanya sendiri.
    try:
        from wth_ai.session_store import SessionStore

        latest = SessionStore(config).load_latest()
        if latest is not None and getattr(latest, "scope", None) is not None:
            state.scope.destructive_allowed = bool(
                getattr(latest.scope, "destructive_allowed", False)
            )
    except Exception:  # noqa: BLE001
        state.scope.destructive_allowed = False
    if no_limits:
        state.scope.destructive_allowed = True

    def _probe(probe: dict[str, Any]) -> Any:
        method = str(probe.get("method", "GET")).upper()
        actor = "recon" if method in {"GET", "HEAD", "OPTIONS"} else "executor"
        return bridge.dispatch_tool("http_probe", probe, actor=actor, approved=False)

    steps = int(meta.get("steps", 6) if meta.get("steps") is not None else 6)
    cycles = int(meta.get("cycles", 2) if meta.get("cycles") is not None else 2)
    # Kebijakan umur checkpoint: `metadata.checkpoint_max_age_seconds`.
    # Tidak diset => 0 => tidak ada batas umur (perilaku lama, kompatibel).
    # Ini keputusan operator yang tertulis di job, bukan default tersembunyi.
    _raw_max_age = meta.get("checkpoint_max_age_seconds")
    try:
        checkpoint_max_age = float(_raw_max_age) if _raw_max_age is not None else 0.0
    except (TypeError, ValueError):
        checkpoint_max_age = 0.0
        ctx.log(
            f"recovery: checkpoint_max_age_seconds tidak valid ({_raw_max_age!r}); "
            "dipakai 0 (tanpa batas umur)"
        )
    if checkpoint_max_age > 0:
        ctx.log(f"recovery: batas umur checkpoint {checkpoint_max_age:.0f}s")
    ctx.progress(30, 100, "autopilot: menjalankan langkah berbatas")
    # Checkpoint/resume: a retried autopilot job continues from the last
    # completed cycle instead of re-probing everything. The store is the same
    # one used for jobs, and checkpoint failure is best-effort (never fails the
    # job). See ADR-014 follow-up.
    store = getattr(ctx, "_manager", None)
    store = getattr(store, "store", None)
    resume_cp = None
    raw_cp = store.load_checkpoint(job.job_id) if store is not None else None
    if raw_cp:
        try:
            from wth_ai.hunt.autopilot import AutopilotCheckpoint

            resume_cp = AutopilotCheckpoint.from_dict(raw_cp)
            ctx.log(
                f"recovery: checkpoint dimuat cursor={resume_cp.cursor} "
                f"completed={len(resume_cp.completed_keys())} "
                f"failed={len(resume_cp.failed_keys())}"
            )
        except Exception as exc:  # noqa: BLE001 - corrupt/unsupported => start fresh
            # Jangan klaim recovery bila checkpoint tidak bisa divalidasi.
            ctx.log(f"recovery: checkpoint ditolak ({type(exc).__name__}); mulai dari awal")
            resume_cp = None
    else:
        ctx.log("recovery: tidak ada checkpoint")

    # Lease checkpoint: satu `run_id` per EKSEKUSI handler, dan `revision`
    # yang naik monoton per penulisan. Kalau eksekusi ini bukan pemegang
    # checkpoint terbaru, CAS menolak dan langkah destruktif berhenti —
    # lebih baik berhenti dengan alasan jelas daripada menimpa progres
    # proses lain secara diam-diam.
    stored_cp = raw_cp or {}
    raw_rev = stored_cp.get("revision", 0)
    base_revision = raw_rev if isinstance(raw_rev, int) and not isinstance(raw_rev, bool) else 0
    stored_run = str(stored_cp.get("run_id", "") or "")
    lease_id = f"{job.job_id}:{int(time.time() * 1000)}:{id(ctx)}"
    revision_counter = {"n": base_revision}

    def _next_revision() -> int:
        # Satu run baru yang mengambil alih harus memakai revision > yang
        # tersimpan; run yang melanjutkan (run_id sama) cukup naik satu.
        if stored_run and stored_run != lease_id:
            revision_counter["n"] = max(revision_counter["n"], base_revision)
        revision_counter["n"] += 1
        return revision_counter["n"]

    def _save_cp(cp: Any) -> None:
        if store is None:
            # Fail closed untuk langkah destruktif: tanpa store tidak ada
            # checkpoint yang bisa dipakai merekonsiliasi efek setelah crash.
            # Langkah non-destruktif tetap berjalan dan degradasinya dilaporkan.
            raise RuntimeError("job store tidak tersedia; checkpoint tidak dapat ditulis")
        payload = cp.to_dict()
        # CAS: kalau proses lain sudah menulis checkpoint yang lebih baru,
        # snapshot ini basi dan TIDAK boleh menimpanya. Menimpanya berarti
        # menghapus langkah yang sudah ditemukan proses itu — kehilangan yang
        # tidak terlihat di log. Kegagalan CAS diperlakukan sama seperti
        # kegagalan tulis: langkah destruktif diblokir, alasannya dilaporkan.
        written = store.save_checkpoint_cas(
            job.job_id, payload, run_id=lease_id, revision=_next_revision(),
        )
        if written is None:
            raise RuntimeError(
                "checkpoint ditolak: proses lain memegang checkpoint yang lebih baru "
                f"(run_id={lease_id})"
            )

    report = run_autopilot(
        target=target, run_probe=_probe, state=state,
        max_cycles=0 if no_limits else max(1, min(6, cycles)),
        max_steps_per_cycle=0 if no_limits else max(1, min(20, steps)),
        destructive_allowed=state.scope.destructive_allowed,
        checkpoint=_save_cp,
        resume=resume_cp,
        # Worker timeouts/cancellations cannot kill the thread; the engine
        # polls this at cycle and step boundaries and persists before exiting.
        should_stop=ctx.should_stop,
        checkpoint_max_age_seconds=checkpoint_max_age,
    )
    # Recovery degradation harus terlihat di log job: "tidak ada checkpoint",
    # "resume ditolak", dan "checkpoint gagal ditulis" adalah tiga keadaan
    # berbeda, dan status akhir job tidak boleh menyamarkan salah satunya.
    if report.resume_rejected_reason:
        ctx.log(f"recovery: resume ditolak — {report.resume_rejected_reason}")
    if report.resource_budget_changed:
        ctx.log(f"recovery: {report.resource_budget_changed}")
    if report.checkpoint_policy_warning:
        ctx.log(f"recovery: {report.checkpoint_policy_warning}")
    if report.checkpoint_error:
        ctx.log(f"recovery: checkpoint gagal ditulis — {report.checkpoint_error}")
    unfinished = [
        step.title for step in report.steps
        if not step.ran and step.blocked_reason
    ]
    if unfinished:
        ctx.log(
            f"autopilot: {len(unfinished)} langkah belum selesai "
            f"(diblokir/butuh verifikasi)"
        )
    ctx.progress(90, 100, "autopilot: menyusun usulan berikutnya")
    from wth_ai.hunt.proactive import propose_next_tests

    proposals = propose_next_tests(findings=[], request_pairs=state.metadata.get("request_pairs", []))
    ctx.progress(100, 100, "autopilot selesai")
    ctx.log(f"autopilot: {report.ran} langkah dijalankan, {report.changed} perubahan")
    # `stop_incomplete` means the run ended at a boundary with unexecuted work
    # (cooperative stop or a destructive step blocked by failed persistence).
    # Reporting that as `completed` would tell the operator the engagement is
    # done when it is not.
    final_status = "stopped" if report.stop_incomplete else "completed"
    if report.stop_incomplete:
        ctx.log(f"autopilot: berhenti belum selesai — {report.stopped_reason}")
    return {
        "type": "autopilot",
        "status": final_status,
        "target": target,
        "report": report.to_dict(),
        "summary": format_report(report),
        "next_tests": [p.to_dict() for p in proposals],
        "destructive_allowed": state.scope.destructive_allowed,
        "resumed_from_cycle": getattr(resume_cp, "cursor", 0) if resume_cp else 0,
        "resume_rejected_reason": report.resume_rejected_reason,
        "resource_budget_changed": report.resource_budget_changed,
        "checkpoint_policy_warning": report.checkpoint_policy_warning,
        "checkpoint_error": report.checkpoint_error,
        "stopped_reason": report.stopped_reason,
        "stop_incomplete": report.stop_incomplete,
        "unfinished_steps": unfinished,
    }


def default_handlers() -> dict[str, Handler]:
    return {
        "recon": handler_recon,
        "executor": handler_executor,
        "report": handler_report,
        "provider_test": handler_provider_test,
        "custom": handler_custom,
        "autopilot": handler_autopilot,
    }
