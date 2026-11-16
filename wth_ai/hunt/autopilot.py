"""Autopilot: jalankan langkah uji berbatas secara otonom, lalu belajar dari hasilnya.

Ini yang mengubah mesin proaktif dari "menyusun saran" menjadi "mengerjakan saran":
satu pass mengambil usulan dari `hunt/proactive.py` + payload berbatas dari
`hunt/payloads.py`, menjalankannya lewat seam `run_probe` (produksi: `http_probe`
dengan scope + audit + rate limit yang sudah ada), mencatat pasangan request/response
sebagai bukti, lalu MENYUSUN ULANG usulan dari apa yang baru dipelajari -- itu
perilaku adaptif, bukan daftar perintah tetap.

Yang TIDAK dilakukan autopilot:
* payload `blast_radius="mass"` (kecuali `WTH_NO_LIMITS`/godmode aktif);
* payload destruktif bila engagement belum membuka opt-in (kecuali no-limits);
* menyentuh target di luar scope (kecuali no-limits, scope check dimatikan);
* mengklaim temuan tanpa bukti -- hasil tiap langkah disimpan apa adanya, dan
  langkah tanpa perubahan dinyatakan negative.

`max_cycles <= 0` dan `max_steps_per_cycle <= 0` berarti tanpa batas.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from wth_ai.hunt.payloads import (
    Payload,
    filter_payloads,
    get_payload,
    payload_definition_digest,
    render_probe,
    render_probe_dict,
)
from wth_ai.jobs.checkpoint import (
    CheckpointEnvelope,
    CheckpointValidationError,
    StepOutcome,
    StepRecord,
    make_step_key,
)
from wth_ai.hunt.proactive import NextTest, propose_next_tests

#: Seam produksi: menerima dict argumen `http_probe`, mengembalikan objek hasil
#: (dengan .stdout/.status_code/.transcript_id). Diuji dengan stub.
ProbeRunner = Callable[[dict[str, Any]], Any]

#: Field probe yang benar-benar membentuk request. `purpose`/`compare_to`
#: sengaja TIDAK ikut: nilainya bisa berubah antar-run (mis. menyebut status
#: 403) dan akan membuat identitas operasi tidak stabil.
_REQUEST_SHAPING_FIELDS = (
    "body", "headers", "cookies", "files", "data", "json", "params",
    "follow_redirects", "max_redirects", "session_clear",
)


def probe_operation_id(probe: Mapping[str, Any]) -> str:
    """Identitas operasi stabil dari sebuah probe.

    `method+url` saja tidak cukup: katalog punya beberapa payload berbeda yang
    menembak URL yang sama (BL-001/BL-002 sama-sama `POST /api/cart`). ID
    payload dipakai lebih dulu karena stabil lintas proses; probe tanpa ID
    memakai digest field yang membentuk request. Key tetap tersedia sebagai
    fallback untuk checkpoint lama.
    """
    method = str(probe.get("method", "GET")).upper()
    url = str(probe.get("url", ""))
    payload_id = str(probe.get("payload_id") or "")
    if payload_id:
        return f"{method} {url}#payload:{payload_id}"
    shape = {
        key: probe[key] for key in _REQUEST_SHAPING_FIELDS if key in probe
    }
    if shape:
        digest = hashlib.sha256(
            json.dumps(shape, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        return f"{method} {url}#shape:{digest}"
    return f"{method} {url}"


@dataclass
class AutopilotStep:
    kind: str            # "proactive" | "payload" | "adaptive"
    title: str
    probe: dict[str, Any]
    ran: bool = False
    blocked_reason: str = ""
    status: int = 0
    changed: bool = False
    evidence_ref: str = ""
    note: str = ""
    #: Identitas operasi (lihat `probe_operation_id`). Dipersistensikan di
    #: engine_state supaya resume tidak bergantung pada rekonstruksi katalog.
    operation_id: str = ""
    #: Digest definisi payload yang membentuk langkah ini (termasuk anchor dan
    #: cleanup). Kosong untuk langkah proaktif yang tidak berasal dari katalog.
    #: Resume membandingkannya dengan katalog saat ini: definisi yang berubah
    #: membuat bukti `COMPLETED` lama tidak boleh dipercaya.
    definition_digest: str = ""
    #: Status eksplisit saat checkpoint dibuat. Kosong = pakai klasifikasi
    #: lama (kompatibel dengan checkpoint sebelum field ini ada).
    outcome: str = ""
    #: Anchor read-only yang diambil SEBELUM operasi destruktif (argumen
    #: `http_probe`). Dibangun ulang dari katalog saat resume; tidak ikut
    #: disimpan ke checkpoint supaya state tetap minimum.
    verify_probe: dict[str, Any] = field(default_factory=dict)
    #: Field yang wajib ada di keadaan jangkar agar keadaannya bisa dibandingkan.
    verify_fields: list[str] = field(default_factory=list)
    #: Request pemulihan (DELETE/PATCH) yang dijalankan setelah efek terverifikasi.
    cleanup_probe: dict[str, Any] = field(default_factory=dict)
    #: Bukti bahwa verifikasi/cleanup benar-benar dilakukan (bukan diklaim).
    verification_ref: str = ""
    cleanup_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "title": self.title, "probe": self.probe, "ran": self.ran,
            "blocked_reason": self.blocked_reason, "status": self.status, "changed": self.changed,
            "evidence_ref": self.evidence_ref, "note": self.note,
            "operation_id": self.operation_id, "outcome": self.outcome,
            "definition_digest": self.definition_digest,
            "verify_fields": list(self.verify_fields),
            "verification_ref": self.verification_ref,
            "cleanup_ref": self.cleanup_ref,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AutopilotStep":
        outcome = str(data.get("outcome", "") or "")
        if outcome:
            try:
                StepOutcome(outcome)
            except ValueError as exc:
                raise CheckpointValidationError(
                    f"AutopilotStep.outcome tidak dikenal: {outcome!r}"
                ) from exc
        return cls(
            kind=str(data.get("kind", "")),
            title=str(data.get("title", "")),
            probe=dict(data.get("probe") or {}),
            ran=bool(data.get("ran", False)),
            blocked_reason=str(data.get("blocked_reason", "")),
            status=int(data.get("status", 0) or 0),
            changed=bool(data.get("changed", False)),
            evidence_ref=str(data.get("evidence_ref", "")),
            note=str(data.get("note", "")),
            operation_id=str(data.get("operation_id", "") or ""),
            outcome=outcome,
            definition_digest=str(data.get("definition_digest", "") or ""),
            verify_fields=[str(x) for x in (data.get("verify_fields") or [])],
            verification_ref=str(data.get("verification_ref", "") or ""),
            cleanup_ref=str(data.get("cleanup_ref", "") or ""),
        )


@dataclass
class AutopilotReport:
    target: str
    cycles: int = 0
    steps: list[AutopilotStep] = field(default_factory=list)
    stopped_reason: str = ""
    destructive_used: bool = False
    #: Envelope checkpoint terakhir. Bukan bagian dari `to_dict()` job hasil
    #: (host menyimpannya terpisah), jadi body/secret tidak merembes ke laporan.
    checkpoint: CheckpointEnvelope | None = field(default=None, repr=False, compare=False)
    resume_rejected_reason: str = ""
    checkpoint_error: str = ""
    #: Perbedaan resource budget antara checkpoint dan proses sekarang.
    #: Bukan alasan menolak completed evidence, tetapi harus terlihat.
    resource_budget_changed: str = ""
    #: Peringatan kebijakan recovery: engagement dengan langkah destruktif
    #: tetapi tanpa batas umur checkpoint. Bukan error; harus terlihat di
    #: log dan hasil job supaya operator bisa memutuskan.
    checkpoint_policy_warning: str = ""
    #: True bila run berhenti karena sinyal stop atau karena langkah
    #: destruktif tidak boleh dijalankan tanpa checkpoint. Handler memakainya
    #: untuk membedakan `completed` dari `stopped`.
    stop_incomplete: bool = False

    @property
    def ran(self) -> int:
        return sum(1 for step in self.steps if step.ran)

    @property
    def changed(self) -> int:
        return sum(1 for step in self.steps if step.changed)

    @property
    def adaptive(self) -> int:
        return sum(1 for step in self.steps if step.kind == "adaptive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target, "cycles": self.cycles, "ran": self.ran,
            "changed": self.changed, "stopped_reason": self.stopped_reason,
            "destructive_used": self.destructive_used,
            "adaptive": self.adaptive,
            "resource_budget_changed": self.resource_budget_changed,
            "checkpoint_policy_warning": self.checkpoint_policy_warning,
            "stop_incomplete": self.stop_incomplete,
            "steps": [step.to_dict() for step in self.steps],
        }


class AutopilotCheckpoint:
    """Adapter tipis teks/lama -> `CheckpointEnvelope` (engine="autopilot").

    Format checkpoint SESUNGGUHNYA adalah `CheckpointEnvelope` (skema
    berversi, engine-agnostic). Kelas ini dipertahankan hanya untuk membaca
    checkpoint lama dan supaya pemanggil lama tidak pecah. State autopilot
    spesifik (riwayat langkah) disimpan di `engine_state["steps"]`.
    """

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> CheckpointEnvelope:
        if isinstance(data, Mapping) and "schema_version" in data:
            return CheckpointEnvelope.from_dict(data)
        # Legacy v1: {target, cycles_done, attempted, steps[]}
        env = CheckpointEnvelope.from_legacy(data, engine="autopilot")
        steps = data.get("steps") or []
        if isinstance(steps, list):
            slimmed = []
            for item in steps:
                if not isinstance(item, Mapping):
                    continue
                probe = item.get("probe")
                slimmed.append({
                    **dict(item),
                    "probe": (
                        {k: probe[k] for k in _RESUME_PROBE_FIELDS if k in probe}
                        if isinstance(probe, Mapping) else {}
                    ),
                })
            env.set_engine_state({"steps": slimmed})
        return env

    @staticmethod
    def to_dict(env: CheckpointEnvelope) -> dict[str, Any]:
        return env.to_dict()


#: Field probe yang diperlukan untuk RESUME. Body/header permintaan tidak
#: disimpan: kandidat dibangun ulang dari katalog, dan checkpoint harus memuat
#: state minimum (bukan salinan payload yang bisa berisi nilai sensitif).
_RESUME_PROBE_FIELDS = ("method", "url", "destructive", "payload_id")


def _slim_step(step: AutopilotStep) -> dict[str, Any]:
    data = step.to_dict()
    probe = data.get("probe") or {}
    data["probe"] = {
        key: probe[key] for key in _RESUME_PROBE_FIELDS if key in probe
    }
    return data


def _stale_definition_ids(steps: Sequence[AutopilotStep]) -> dict[str, str]:
    """Payload ID yang digest definisinya tidak cocok dengan katalog sekarang.

    Hanya record yang MEMILIKI digest yang diperiksa: checkpoint lama tanpa
    digest tidak pernah dituduh berubah. Bila sudah ada record lain untuk
    payload yang sama dengan digest saat ini, record lama dianggap historis —
    resume setelah langkah dijalankan ulang tidak boleh menolak terus-menerus.

    Nilai mapping: digest saat ini, atau penanda bila payload sudah tidak ada.
    """
    records: dict[str, list[AutopilotStep]] = {}
    for step in steps:
        payload_id = str(step.probe.get("payload_id") or "")
        if payload_id and step.definition_digest:
            records.setdefault(payload_id, []).append(step)

    stale: dict[str, str] = {}
    for payload_id, prior_steps in records.items():
        payload = get_payload(payload_id)
        current = payload_definition_digest(payload) if payload is not None else ""
        if any(step.definition_digest == current for step in prior_steps):
            continue
        stale[payload_id] = current or "(tidak ada di katalog)"
    return stale


def _checkpoint_from_steps(
    *,
    target: str,
    cursor: int,
    steps: list[AutopilotStep],
    resource_budget: Mapping[str, int] | None = None,
) -> CheckpointEnvelope:
    env = CheckpointEnvelope(
        engine="autopilot", target=target, cursor=cursor,
        resource_budget=dict(resource_budget or {}),
    )
    # `saved_at` dicap di sini supaya kebijakan umur punya data nyata. Tanpa
    # cap, `is_stale()` selalu False dan TTL tidak akan pernah berlaku.
    from datetime import datetime, timezone

    env.saved_at = datetime.now(timezone.utc).isoformat()
    for step in steps:
        key = make_step_key(step.probe.get("method", "GET"), step.probe.get("url", ""))
        if not step.probe.get("url"):
            continue
        identity = step.operation_id or probe_operation_id(step.probe)
        step.operation_id = identity
        destructive = bool(step.probe.get("destructive"))
        explicit = StepOutcome(step.outcome) if step.outcome else None
        if step.note.startswith("dry-run"):
            # Dry-run eksplisit: belum pernah dieksekusi, bukan in-flight.
            outcome, verified = StepOutcome.PENDING, False
        elif explicit is not None:
            # Status dari engine adalah kebenaran utama. Langkah destruktif
            # TIDAK boleh menjadi COMPLETED hanya karena menerima response:
            # status HTTP membuktikan request selesai, bukan efek state-nya.
            # `COMPLETED` destruktif hanya sah bila verifier jangkar mencatat
            # bukti (`verification_ref`) — bukan klaim dari engine saja.
            if explicit == StepOutcome.COMPLETED and destructive:
                clean = bool(step.verification_ref)
                outcome, verified = (
                    (StepOutcome.COMPLETED, True) if clean
                    else (StepOutcome.UNKNOWN, False)
                )
            else:
                outcome = explicit
                verified = outcome == StepOutcome.COMPLETED
        elif step.ran and step.status and not step.note.startswith("gagal"):
            if destructive:
                outcome, verified = StepOutcome.UNKNOWN, False
            else:
                outcome, verified = StepOutcome.COMPLETED, True
        elif step.ran:
            # "ran" tanpa status/evidence bukan bukti selesai (mis. diblokir
            # scope dengan status 0). Jangan pernah dianggap completed.
            outcome, verified = StepOutcome.FAILED, False
        elif step.blocked_reason:
            outcome, verified = StepOutcome.FAILED, False
        else:
            outcome, verified = StepOutcome.INFLIGHT, False
        env.mark(
            key, outcome, verified=verified, status=step.status,
            evidence_ref=step.evidence_ref, note=step.note,
            operation_id=identity,
        )
    env.set_engine_state({"steps": [_slim_step(step) for step in steps]})
    return env


def _safe_probe(run_probe: ProbeRunner, probe: Mapping[str, Any]) -> Any:
    """Baca anchor/cleanup tanpa membiarkan kegagalannya menghentikan run."""
    try:
        return run_probe(dict(probe))
    except Exception:  # noqa: BLE001
        return None


def _verify_destructive_effect(
    *, step: AutopilotStep, run_probe: ProbeRunner, before_body: str,
    before_posture: Mapping[str, Any] | None = None,
) -> Any:
    """Apakah efek langkah destruktif sudah tidak ada di keadaan target?

    Anchor diambil sebelum operasi (`before_body`) supaya pembacaan sesudah
    tidak bisa "membuktikan" keadaan yang sudah tercemar. `before_posture`
    (status + header saat itu) diteruskan hanya untuk alasan penolakan yang
    lebih konkret — lihat `posture_diff` di `destructive_verify`.
    """
    from wth_ai.hunt.destructive_verify import EffectAnchor, verify_effect

    payload_id = str(step.probe.get("payload_id") or "")
    anchor = (
        EffectAnchor(
            probe=dict(step.verify_probe),
            expected=tuple(step.verify_fields),
            description=step.title,
        )
        if step.verify_probe
        else None
    )
    return verify_effect(
        payload_id=payload_id,
        anchor=anchor,
        run_probe=run_probe,
        before_body=before_body,
        requires_cleanup=bool(step.cleanup_probe),
        before_posture=before_posture,
    )


def _run_cleanup(
    *, step: AutopilotStep, run_probe: ProbeRunner, before_had_marker: bool | None = None
) -> str:
    """Jalankan request pemulihan dan laporkan hasil SEBENARNYA.

    Catalog `cleanup` bisa berupa instruksi manual; hanya `cleanup_probe`
    yang deklaratif dan aman dijalankan mesin. Hasil cleanup yang gagal
    dicatat apa adanya — efek samping yang tertinggal bukan hal yang boleh
    disamarkan oleh status sukses.
    """
    from wth_ai.hunt.destructive_verify import EffectAnchor, verify_cleanup

    if not step.cleanup_probe:
        return "pemulihan manual (tidak ada request cleanup deklaratif)"

    result = _safe_probe(run_probe, step.cleanup_probe)
    status = int(getattr(result, "status_code", 0) or 0)
    ref = str(getattr(result, "transcript_id", "") or "")
    step.cleanup_ref = f"{status} {ref}".strip()
    if not status or status >= 400:
        return f"cleanup gagal (status {status or 'tidak ada'}); pulihkan manual"

    check = verify_cleanup(
        payload_id=str(step.probe.get("payload_id") or ""),
        anchor=EffectAnchor(
            probe=dict(step.verify_probe), expected=tuple(step.verify_fields)
        ) if step.verify_probe else None,
        run_probe=run_probe,
        before_had_marker=before_had_marker,
    )
    if check.verified:
        step.verification_ref = f"{step.verification_ref}; {check.reason}"
        return f"cleanup {status} terverifikasi: {check.reason}"
    return f"cleanup status {status}, tetapi {check.reason}"


def _payload_to_step(payload: Payload, values: Mapping[str, Any]) -> AutopilotStep:
    probe = render_probe(payload, **values)
    step = AutopilotStep(
        kind="payload",
        title=f"{payload.id} {payload.title}",
        probe={**probe, "payload_id": payload.id, "purpose": payload.title,
               "destructive": payload.requires_destructive},
        note=payload.cleanup,
        definition_digest=payload_definition_digest(payload),
    )
    # Anchor read-only, dirakit di sini (sebelum eksekusi) dari metadata
    # deklaratif payload. Ia TIDAK disimpan ke engine_state (lihat
    # `_RESUME_PROBE_FIELDS`): resume membangunnya lagi dari katalog.
    #
    # Semua request bantu (anchor sebelum/sesudah, verifikasi cleanup) diberi
    # `mutating: False` di sini, satu tempat: probe jangkar WAJIB tidak
    # mengubah target, dan penanda itu membuat pemanggil (test harness,
    # counter operasi, audit) bisa membedakan "request bantu" dari "operasi
    # yang sedang diuji" tanpa menebak dari method/URL.
    if payload.verify_probe:
        step.verify_probe = {
            **render_probe_dict(payload.verify_probe, values), "mutating": False,
        }
        step.verify_fields = list(payload.verify_fields)
    elif payload.requires_destructive:
        # Payload tanpa anchor deklaratif (biasanya hasil generate `GEN-*`)
        # tidak boleh berakhir tanpa verifikasi sama sekali. Anchor generik
        # menunjuk objek yang disentuh operasi dengan method read-only yang
        # sama, dan verifier-nya berbasis marker yang disuntikkan payload:
        # tanpa transisi marker yang terlihat, verdict tetap `UNKNOWN`.
        step.verify_probe = {
            **generic_verify_probe(probe), "mutating": False,
        }
    if payload.cleanup_probe:
        step.cleanup_probe = {
            **render_probe_dict(payload.cleanup_probe, values), "mutating": False,
        }
    return step


def generic_verify_probe(probe: Mapping[str, Any]) -> dict[str, Any]:
    """Anchor read-only generik untuk payload tanpa `verify_probe`.

    Aturannya sempit dan sengaja konservatif:

    * HEAD bila operasi sendiri HEAD/OPTIONS; selain itu GET, karena HEAD
      sering tidak mengembalikan body sehingga marker tidak bisa dibaca.
    * Parameter/body operasi tidak dibawa: anchor harus membaca keadaan, bukan
      mengulang operasi. Hanya `url` (dan path-nya) yang dipakai bersama.
    * Header tidak dibawa (Host/kustom lain bisa mengubah respons).

    Anchor ini hanya bermakna bila verifier menemukan perubahan marker; tanpa
    itu verdict tetap `UNKNOWN` — lihat `destructive_verify.verify_effect`.
    """
    method = str(probe.get("method", "GET") or "GET").upper()
    verify_method = "HEAD" if method in {"HEAD", "OPTIONS"} else "GET"
    out: dict[str, Any] = {
        "method": verify_method,
        "url": str(probe.get("url", "") or ""),
    }
    params = probe.get("params")
    if verify_method == "HEAD" and isinstance(params, Mapping) and params:
        # HEAD biasanya hanya membuktikan keberadaan resource. Tanpa param,
        # anchor menunjuk objek induk; itu akan dilaporkan sebagai
        # `unavailable`, bukan diklaim sukses.
        out["params"] = dict(params)
    return out


def _next_to_step(test: NextTest) -> AutopilotStep:
    # Metadata risiko dari `NextTest` dibawa masuk ke probe step. Tanpa ini,
    # saran proaktif yang mengubah state (mis. POST) akan berjalan sebagai
    # langkah "biasa" dan lolos dari gerbang destruktif + checkpoint INFLIGHT.
    probe = dict(test.probe)
    probe.setdefault("destructive", bool(test.destructive))
    probe["risk"] = test.risk
    probe["blast_radius"] = test.blast_radius
    return AutopilotStep(kind="proactive", title=test.title, probe=probe,
                         note=test.rationale)


def run_autopilot(
    *,
    target: str,
    run_probe: ProbeRunner,
    state: Any = None,
    max_cycles: int = 3,
    max_steps_per_cycle: int = 6,
    destructive_allowed: bool | None = None,
    vuln_class: str | None = None,
    payload_ids: Sequence[str] = (),
    dry_run: bool = False,
    learn: Callable[[AutopilotStep], None] | None = None,
    adaptive: bool = True,
    adaptive_moves: int = 3,
    adaptive_budget: int = 12,
    engagement: Any = None,
    checkpoint: Callable[[CheckpointEnvelope], None] | None = None,
    resume: CheckpointEnvelope | None = None,
    should_stop: Callable[[], bool] | None = None,
    checkpoint_max_age_seconds: float | None = None,
) -> AutopilotReport:
    """Jalankan sampai `max_cycles` pass adaptif.

    Setiap cycle: susun kandidat (usulan proaktif + payload berbatas) -> jalankan ->
    catat bukti -> susun ulang dari hasil. Berhenti bila tidak ada kandidat baru atau
    langkah habis.

    `should_stop` dipanggil di batas cycle dan sebelum setiap step. Ini penting
    untuk worker yang men-timeout job: thread handler tidak bisa dibunuh, jadi
    satu-satunya cara berhenti aman adalah sinyal kooperatif.

    `checkpoint_max_age_seconds`: kebijakan umur checkpoint. `None`/`<= 0`
    berarti resume mengikuti checkpoint tanpa batas umur (perilaku lama), dan
    checkpoint tanpa `saved_at` tidak pernah dianggap basi. Bila diisi dan
    checkpoint lebih tua, resume DITOLAK dengan alasan eksplisit — bukan diam-diam
    dilanjutkan dengan state yang mungkin sudah tidak mencerminkan target.
    """
    from wth_ai.engagement.mode import is_no_limits

    no_limits = is_no_limits(getattr(state, "config", None))
    if destructive_allowed is None:
        destructive_allowed = no_limits or bool(getattr(getattr(state, "scope", None), "destructive_allowed", False))

    cycle_limit = max_cycles if max_cycles > 0 else 10**9
    step_limit = max_steps_per_cycle if max_steps_per_cycle > 0 else 10**9

    report = AutopilotReport(target=target)
    if destructive_allowed and not (checkpoint_max_age_seconds or 0) > 0:
        # TTL opt-in tetap dipertahankan (tanpa default tersembunyi), tetapi
        # engagement yang mengizinkan langkah destruktif tidak boleh diam
        # tentang risikonya: checkpoint `COMPLETED` bisa di-resume tanpa batas
        # umur, padahal keadaan target (objek, sesi, id) bisa sudah berubah.
        report.checkpoint_policy_warning = (
            "checkpoint_max_age_seconds tidak diset untuk engagement dengan "
            "langkah destruktif: bukti completed dapat di-resume tanpa batas "
            "umur. Set metadata.checkpoint_max_age_seconds agar checkpoint "
            "lama tidak dipakai setelah keadaan target berubah."
        )
    metadata = getattr(state, "metadata", None)
    if metadata is None and state is not None:
        metadata = getattr(state, "metadata", {})
    pairs: list[dict[str, Any]] = metadata.setdefault("request_pairs", []) if metadata is not None else []

    findings = []
    if state is not None:
        try:
            findings = [f.to_dict() if hasattr(f, "to_dict") else dict(f)
                        for f in getattr(state, "_findings_source", lambda: [])()]
        except Exception:  # noqa: BLE001
            findings = []

    # `{base}` WAJIB berupa URL berskema: versi pertama memakai target apa adanya
    # ("127.0.0.1:42969"), sehingga `requests` melempar MissingSchema dan setiap
    # langkah tercatat "dijalankan" dengan status kosong -- nol permintaan nyata.
    from wth_ai.recon.http import normalize_url

    values = {
        "base": normalize_url(target).rstrip("/"),
        "marker": "wth-auto-9z8y7",
        "own_id": "1",
        "own_other_id": "1",
        "other_id": "2",
        "own_email": "wth-test@example.invalid",
        "bucket": "wth-test-bucket-9z8y7",
        "none_jwt": "eyJhbGciOiJub25lIn0.e30.",
        "confusion_jwt": "wth-confusion-token",
    }

    # Hanya step yang benar-benar selesai DAN terverifikasi yang dilewati.
    # Identitas operasi dipakai lebih dulu; key hanya fallback untuk record
    # legacy yang belum punya `operation_id`.
    completed_operations: set[str] = set()
    completed_legacy_keys: set[str] = set()
    start_cycles = 0
    current_budget = {
        "max_cycles": max(0, max_cycles),
        "max_steps_per_cycle": max(0, max_steps_per_cycle),
    }
    if resume is not None:
        if not resume.matches_target(target):
            # Checkpoint target lain tidak pernah diterapkan: memakainya bisa
            # menumpulkan permukaan yang belum diuji untuk target ini.
            report.resume_rejected_reason = (
                f"target checkpoint '{resume.target}' != '{target}'"
            )
            resume = None
        elif resume.is_stale(checkpoint_max_age_seconds or 0):
            # Kebijakan umur eksplisit: state lama bisa sudah tidak
            # mencerminkan target (objek berubah, sesi kedaluwarsa). Resume
            # yang tetap memakai `completed` lama akan MELEWATI langkah yang
            # sebenarnya perlu diulang. Tolak dengan alasan yang bisa dibaca,
            # bukan lanjut diam-diam.
            age = resume.age_seconds()
            report.resume_rejected_reason = (
                f"checkpoint terlalu tua: umur {age:.0f}s > batas "
                f"{checkpoint_max_age_seconds:.0f}s "
                f"(saved_at={resume.saved_at})"
            )
            resume = None
        else:
            completed_operations = resume.completed_operations()
            completed_legacy_keys = resume.completed_legacy_keys()
            start_cycles = min(resume.cursor, cycle_limit)
            report.cycles = start_cycles
            prior_steps = resume.engine_state.get("steps") or []
            if isinstance(prior_steps, list):
                report.steps = [AutopilotStep.from_dict(x) for x in prior_steps
                                if isinstance(x, Mapping)]
                for prior in report.steps:
                    if not prior.outcome and not prior.note.startswith("dry-run"):
                        # Checkpoint tanpa status eksplisit tidak membawa bukti
                        # verifikasi. Jangan menaikkannya menjadi COMPLETED
                        # hanya dari `ran`/status lama; perlakukan sebagai
                        # UNKNOWN supaya tidak pernah dilewati saat resume.
                        prior.outcome = StepOutcome.UNKNOWN.value

            # Digest definisi mendeteksi katalog yang berubah antar-proses.
            # Anchor/verify_probe TIDAK disimpan di checkpoint dan dibangun
            # ulang dari katalog; bila definisinya berubah, bukti COMPLETED
            # lama menyatakan hal yang berbeda dari operasi yang akan
            # dijalankan sekarang. Jangan percayai bukti itu.
            stale = _stale_definition_ids(report.steps)
            if stale:
                details = ", ".join(
                    f"{payload_id} ({current_digest})"
                    for payload_id, current_digest in sorted(stale.items())
                )
                report.resume_rejected_reason = (
                    "definisi payload berubah antar-proses: "
                    f"{details}; bukti completed lama tidak dipakai"
                )
                stale_ids = set(stale)
                for prior in report.steps:
                    payload_id = str(prior.probe.get("payload_id") or "")
                    if payload_id not in stale_ids:
                        continue
                    prior.outcome = StepOutcome.UNKNOWN.value
                    if prior.probe.get("destructive"):
                        prior.blocked_reason = (
                            "definisi payload berubah antar-proses; "
                            "verifikasi keadaan sebelum mengulang"
                        )
                completed_operations -= {
                    prior.operation_id or probe_operation_id(prior.probe)
                    for prior in report.steps
                    if str(prior.probe.get("payload_id") or "") in stale_ids
                }
            if resume.resource_budget and resume.resource_budget != current_budget:
                keys = sorted(set(resume.resource_budget) | set(current_budget))
                changes = [
                    f"{key}: {resume.resource_budget.get(key, 'tidak ada')} -> "
                    f"{current_budget.get(key, 'tidak ada')}"
                    for key in keys
                    if resume.resource_budget.get(key) != current_budget.get(key)
                ]
                report.resource_budget_changed = (
                    "resource budget berubah antar-proses: " + "; ".join(changes)
                )
            if resume.cursor > cycle_limit:
                report.resume_rejected_reason = (
                    f"cursor {resume.cursor} melebihi max_cycles {cycle_limit}; "
                    f"dipangkas ke {cycle_limit}"
                )
            if start_cycles >= cycle_limit:
                report.stopped_reason = (
                    f"resume: {start_cycles}/{cycle_limit} cycle sudah selesai"
                )

    adaptive_left = adaptive_budget if adaptive_budget > 0 else 10**9

    # Langkah destruktif yang statusnya tidak terverifikasi TIDAK diulang
    # otomatis: pengulangan bisa menggandakan efek samping (refund ganda,
    # order kedua, objek terhapus lagi). Operator harus memverifikasi keadaan
    # dulu (lihat `cleanup` pada note). Ini satu-satunya kelas langkah yang
    # tidak mengikuti aturan "retry jika bukan COMPLETED".
    needs_verification: dict[str, str] = {}
    needs_verification_keys: dict[str, str] = {}
    for prior in report.steps:
        prior_key = make_step_key(
            prior.probe.get("method", "GET"), prior.probe.get("url", "")
        )
        if not prior.probe.get("url"):
            continue
        # Apakah record ini sudah punya identitas operasi eksplisit?
        # `engine_state` baru selalu menyimpannya. Record legacy tidak; untuk
        # itu key dipakai sebagai fallback -- dan HANYA untuk legacy, supaya
        # langkah yang tidak diketahui hasilnya tidak memblokir operasi lain
        # yang kebetulan memakai method+URL sama.
        identified = bool(prior.operation_id) or bool(prior.probe.get("payload_id"))
        prior_identity = prior.operation_id or probe_operation_id(prior.probe)
        prior.operation_id = prior_identity
        if prior.probe.get("destructive") and not prior.note.startswith("dry-run"):
            done = (
                prior_identity in completed_operations
                or prior_key in completed_legacy_keys
            )
            if not done:
                cleanup = prior.note or "tidak ada catatan cleanup"
                reason = (
                    "langkah destruktif belum terverifikasi selesai; verifikasi "
                    f"keadaan sebelum mengulang — {cleanup}"
                )
                needs_verification[prior_identity] = reason
                if not identified:
                    needs_verification_keys[prior_key] = reason
                prior.ran = False
                prior.blocked_reason = reason

    def _save_checkpoint(cycles_done: int) -> bool:
        """Persist snapshot; return False bila persistence yang dikonfigurasi gagal.

        `False` bukan "run gagal": pemanggil memutuskan apakah kegagalan itu
        fatal (langkah destruktif) atau hanya degradasi yang dilaporkan
        (langkah non-destruktif). Error tetap disimpan terpisah dari
        `stopped_reason`.
        """
        env = _checkpoint_from_steps(
            target=target, cursor=cycles_done, steps=list(report.steps),
            resource_budget=current_budget,
        )
        # Pertahankan record completed yang tidak muncul di riwayat run ini
        # (mis. selesai di proses sebelumnya) supaya resume berikutnya tidak
        # kehilangan bukti. Untuk record ber-identitas, `mark` memperbarui
        # record yang sama; untuk record legacy tanpa identitas, tambahkan
        # sebagai record terpisah agar tidak menimpa operasi lain dengan key
        # yang kebetulan sama.
        for prior in resume.steps if resume is not None else []:
            if prior.outcome != StepOutcome.COMPLETED or not prior.verified:
                continue
            if prior.operation_id:
                env.mark(
                    prior.key, StepOutcome.COMPLETED, verified=True,
                    operation_id=prior.operation_id, status=prior.status,
                    evidence_ref=prior.evidence_ref, note=prior.note,
                )
            else:
                # Record legacy tanpa identitas: bila engine_state memberi
                # record UNKNOWN dengan key sama (dari langkah prior yang
                # sama), naikkan record itu tanpa menggandakannya. Jika tidak,
                # tambahkan record terpisah -- jangan menimpa record baru
                # yang kebetulan memakai key sama (operasi berbeda).
                unknown = next(
                    (
                        s for s in env.steps
                        if s.key == prior.key and s.outcome == StepOutcome.UNKNOWN
                    ),
                    None,
                )
                if unknown is not None:
                    env.mark(
                        prior.key, StepOutcome.COMPLETED, verified=True,
                        operation_id=unknown.operation_id,
                        status=prior.status, evidence_ref=prior.evidence_ref,
                        note=prior.note,
                    )
                elif not any(
                    s.key == prior.key and not s.operation_id for s in env.steps
                ):
                    env.steps.append(StepRecord(
                        key=prior.key, outcome=StepOutcome.COMPLETED, verified=True,
                        status=prior.status, evidence_ref=prior.evidence_ref,
                        note=prior.note,
                    ))
        # Retry state harus kumulatif lintas run: `_checkpoint_from_steps`
        # membangun ulang dari riwayat run ini, jadi tambahkan attempts dari
        # checkpoint yang di-resume (kalau ada) alih-alih mereset ke 1.
        # Fallback key hanya dipakai bila salah satu record memang legacy
        # (tanpa identitas); dua operasi berbeda dengan key sama tidak boleh
        # saling mewarisi jumlah percobaan.
        if resume is not None:
            for rec in env.steps:
                prev = resume.step_by_identity(rec.identity) if rec.operation_id else None
                if prev is None:
                    candidate = resume.step(rec.key)
                    if candidate is not None and (
                        not rec.operation_id or not candidate.operation_id
                    ):
                        prev = candidate
                if prev is not None:
                    rec.attempts += prev.attempts
        report.checkpoint = env
        if checkpoint is None:
            return True
        try:
            checkpoint(env)
        except Exception as exc:  # noqa: BLE001 - checkpoint failure is recorded, not fatal by itself
            # Recovery degradation is reported SEPARATELY from why the run
            # stopped; never overload `stopped_reason` with a storage failure.
            # Error tidak dihapus oleh save berikutnya: kegagalan sementara
            # tetap merupakan degradasi yang harus terlihat.
            report.checkpoint_error = f"{type(exc).__name__}: {exc}"
            return False
        return True

    # Snapshot awal: kalau proses mati di tengah cycle pertama, checkpoint
    # sudah ada (cursor = start_cycles), bukan hanya di memori. Tanpa ini,
    # resume yang langsung kehabisan cycle (atau crash sebelum cycle pertama
    # selesai) meninggalkan `report.checkpoint is None`.
    _save_checkpoint(start_cycles)

    attempted_operations: set[str] = set()
    current_run_steps: set[int] = set()
    seen_blocked: set[str] = set()
    stop_requested = False
    if should_stop is not None and should_stop():
        report.stopped_reason = "dihentikan sebelum cycle pertama"
        report.stop_incomplete = True
        stop_requested = True

    for cycle in range(start_cycles + 1, cycle_limit + 1):
        if stop_requested:
            break
        if should_stop is not None and should_stop():
            report.stopped_reason = f"dihentikan sebelum cycle {cycle}"
            report.stop_incomplete = True
            stop_requested = True
            break
        report.cycles = cycle
        candidates: list[AutopilotStep] = []

        # (a) payload eksplisit yang diminta operator
        for payload_id in payload_ids:
            payload = get_payload(payload_id)
            if payload is None:
                continue
            # Guard yang tidak bisa dilewati permintaan eksplisit: payload massal
            # (auto_runnable=False) TIDAK PERNAH dijalankan mesin. Terukur: versi
            # pertama hanya menyaring di jalur otomatis, sehingga `payload_ids=[IW-004]`
            # lolos ke kandidat -- tepat properti yang dijanjikan katalog.
            if (payload.blast_radius == "mass" or not payload.auto_runnable) and not no_limits:
                candidates.append(AutopilotStep(
                    kind="payload", title=f"{payload.id} {payload.title}", probe={}, ran=False,
                    blocked_reason=(
                        "payload berdampak massal — tidak pernah dijalankan mesin. "
                        f"Pakai padanan berbatas: {payload.bounded_alternative or 'lihat katalog'}"
                    ),
                ))
                continue
            if payload.requires_destructive and not destructive_allowed and not no_limits:
                candidates.append(AutopilotStep(
                    kind="payload", title=f"{payload.id} {payload.title}", probe={}, ran=False,
                    blocked_reason="payload destruktif tapi engagement belum membuka opt-in",
                ))
                continue
            candidates.append(_payload_to_step(payload, values))

        # (b) usulan proaktif (temuan + sinyal respons)
        for test in propose_next_tests(findings=findings, request_pairs=pairs,
                                      limit=step_limit):
            candidates.append(_next_to_step(test))

        # (c) payload berbatas yang relevan dengan kelas yang diminta
        for payload in filter_payloads(
            vuln_class,
            max_blast="mass" if no_limits else "single-object",
            include_destructive=destructive_allowed or no_limits,
            auto_only=not no_limits,
        ):
            if (payload.blast_radius == "mass" or not payload.auto_runnable) and not no_limits:
                continue  # dua guard yang sama, jalur otomatis
            candidates.append(_payload_to_step(payload, values))

        fresh: list[AutopilotStep] = []
        seen_fresh: set[str] = set()
        for c in candidates:
            key = make_step_key(c.probe.get("method", "GET"), c.probe.get("url", ""))
            if not c.probe.get("url"):
                marker = f"{c.kind}|{c.title}|{c.blocked_reason}"
                if marker not in seen_blocked:
                    seen_blocked.add(marker)
                    fresh.append(c)
                continue
            identity = c.operation_id or probe_operation_id(c.probe)
            c.operation_id = identity
            if identity in seen_fresh or identity in attempted_operations:
                continue
            if identity in completed_operations or key in completed_legacy_keys:
                continue  # benar-benar selesai & terverifikasi
            blocked_reason = needs_verification.get(identity) or needs_verification_keys.get(key)
            if blocked_reason:
                # Jangan jalankan ulang; tandai langkah prior (dari checkpoint)
                # supaya alasannya terlihat di laporan. Langkah yang sudah
                # berjalan di run ini tidak diubah retroaktif.
                for prior in report.steps:
                    prior_identity = prior.operation_id or probe_operation_id(prior.probe)
                    prior_key = make_step_key(
                        prior.probe.get("method", "GET"), prior.probe.get("url", "")
                    )
                    if prior_identity == identity or prior_key == key:
                        if id(prior) not in current_run_steps:
                            prior.ran = False
                            prior.blocked_reason = blocked_reason
                        break
                continue
            seen_fresh.add(identity)
            fresh.append(c)
        if not fresh:
            report.stopped_reason = "tidak ada kandidat baru (semua sudah dicoba)"
            break

        for step in fresh[: max(1, step_limit)]:
            if should_stop is not None and should_stop():
                report.stopped_reason = f"dihentikan di tengah cycle {cycle}"
                report.stop_incomplete = True
                stop_requested = True
                break
            if not step.probe or not step.probe.get("url"):
                report.steps.append(step)
                continue
            identity = step.operation_id or probe_operation_id(step.probe)
            step.operation_id = identity
            attempted_operations.add(identity)
            destructive = bool(step.probe.get("destructive"))
            if destructive_allowed and destructive:
                report.destructive_used = True
            if dry_run:
                step.note = (step.note + " | dry-run: tidak dijalankan").strip(" |")
                step.outcome = StepOutcome.PENDING.value
                report.steps.append(step)
                continue

            # Kontrak crash-consistency: INFLIGHT harus SUDAH ada di storage
            # sebelum request dikirim. Kalau proses mati setelah ini, resume
            # tahu ada operasi yang mungkin sudah menyentuh target.
            step.outcome = StepOutcome.INFLIGHT.value
            report.steps.append(step)
            current_run_steps.add(id(step))
            persisted = _save_checkpoint(cycle - 1)
            if destructive and not persisted:
                # Tanpa checkpoint, efek operasi destruktif tidak bisa
                # direkonsiliasi setelah crash. Jangan jalan sama sekali.
                step.outcome = StepOutcome.PENDING.value
                step.blocked_reason = (
                    "tidak dijalankan: checkpoint INFLIGHT gagal ditulis, sehingga "
                    "hasil operasi destruktif tidak bisa dipulihkan dengan aman"
                )
                report.stopped_reason = (
                    "checkpoint tidak dapat ditulis; langkah destruktif tidak dijalankan"
                )
                report.stop_incomplete = True
                _save_checkpoint(cycle - 1)
                stop_requested = True
                break

            cleanup = step.note or "tidak ada catatan cleanup"
            # Anchor diambil SEBELUM operasi destruktif. Urutannya penting:
            # pembacaan sesudah tidak boleh memakai keadaan yang sudah
            # tercemar oleh operasi itu sendiri sebagai pembanding "awal".
            anchor_before = ""
            anchor_posture: dict[str, Any] = {}
            anchor_had_marker = False
            if destructive and step.verify_probe:
                from wth_ai.hunt.destructive_verify import DEFAULT_EFFECT_MARKER

                anchor_like = _safe_probe(run_probe, step.verify_probe)
                anchor_before = str(getattr(anchor_like, "stdout", "") or "")
                headers = getattr(anchor_like, "response_headers", None) or {}
                anchor_posture = {
                    "status": str(getattr(anchor_like, "status_code", "") or ""),
                    "headers": {
                        str(key).lower(): str(value).strip()
                        for key, value in headers.items()
                    } if isinstance(headers, Mapping) else {},
                }
                anchor_had_marker = DEFAULT_EFFECT_MARKER in anchor_before.lower()
            try:
                result = run_probe(step.probe)
            except Exception as exc:  # noqa: BLE001
                # Exception tidak membuktikan request tidak pernah sampai.
                # Status UNKNOWN jujur; langkah destruktif tidak boleh diulang
                # otomatis sampai keadaan target diverifikasi.
                step.outcome = StepOutcome.UNKNOWN.value
                step.note = (
                    f"gagal dijalankan: {type(exc).__name__}; hasil tidak diketahui"
                )
                if destructive:
                    reason = (
                        "langkah destruktif gagal/terputus dan hasilnya tidak diketahui; "
                        f"verifikasi keadaan sebelum mengulang — {cleanup}"
                    )
                    needs_verification[identity] = reason
                    step.note = (
                        f"{step.note} | verifikasi keadaan sebelum mengulang — {cleanup}"
                    )
                _save_checkpoint(cycle - 1)
                if should_stop is not None and should_stop():
                    report.stopped_reason = f"dihentikan di tengah cycle {cycle}"
                    report.stop_incomplete = True
                    stop_requested = True
                    break
                continue

            step.ran = True
            status = int(getattr(result, "status_code", 0) or 0)
            step.status = status
            step.evidence_ref = str(getattr(result, "transcript_id", "") or "")
            body = str(getattr(result, "stdout", "") or "")
            if status:
                if destructive:
                    # Response membuktikan request selesai, BUKAN bahwa efek
                    # state yang diinginkan terjadi — kecuali verifier jangkar
                    # membuktikan keadaan target sudah kembali bersih. Tanpa
                    # bukti itu, status yang jujur adalah UNKNOWN.
                    verification = _verify_destructive_effect(
                        step=step, run_probe=run_probe, before_body=anchor_before,
                        before_posture=anchor_posture,
                    )
                    if verification.verified:
                        step.outcome = StepOutcome.COMPLETED.value
                        step.verification_ref = verification.reason
                        step.note = (
                            f"{step.note} | status={status}; efek terverifikasi: "
                            f"{verification.reason}"
                        ).strip(" |")
                        step.changed = False
                        cleanup_note = _run_cleanup(
                            step=step, run_probe=run_probe,
                            before_had_marker=anchor_had_marker,
                        )
                        if cleanup_note:
                            step.note = f"{step.note} | {cleanup_note}".strip(" |")
                    else:
                        step.outcome = StepOutcome.UNKNOWN.value
                        reason = (
                            f"langkah destruktif menerima status {status}, tetapi "
                            f"{verification.reason}; verifikasi sebelum mengulang — {cleanup}"
                        )
                        needs_verification[identity] = reason
                        step.note = (
                            f"{step.note} | status={status}; {verification.reason}; "
                            f"verifikasi sebelum mengulang — {cleanup}"
                        ).strip(" |")
                        # Efek yang MASIH terlihat adalah kasus yang paling
                        # perlu pemulihan, bukan alasan untuk melewatinya.
                        # Cleanup tetap dicoba bila deklaratif, dan hasilnya
                        # dilaporkan apa adanya — sukses cleanup tidak
                        # mengubah verdict efek menjadi COMPLETED, karena
                        # pemulihan bukan bukti bahwa bug-nya tidak ada.
                        cleanup_note = _run_cleanup(
                            step=step, run_probe=run_probe,
                            before_had_marker=anchor_had_marker,
                        )
                        if cleanup_note:
                            step.note = f"{step.note} | {cleanup_note}".strip(" |")
                else:
                    step.outcome = StepOutcome.COMPLETED.value
            else:
                step.outcome = StepOutcome.FAILED.value
                if destructive:
                    reason = (
                        "langkah destruktif tidak menghasilkan bukti respons; "
                        f"verifikasi keadaan sebelum mengulang — {cleanup}"
                    )
                    needs_verification[identity] = reason
                    step.note = (
                        f"{step.note} | tidak ada bukti respons; verifikasi keadaan "
                        f"sebelum mengulang — {cleanup}"
                    ).strip(" |")
            if status and status >= 500:
                step.note = (step.note + f" | status={status} (perhatikan)").strip(" |")

            # Simpan hasil SEBELUM verifikasi/adaptive/learning: crash setelah
            # respons tidak boleh menghapus bukti bahwa operasi sudah berjalan.
            _save_checkpoint(cycle - 1)
            if should_stop is not None and should_stop():
                report.stopped_reason = f"dihentikan di tengah cycle {cycle}"
                report.stop_incomplete = True
                stop_requested = True
                break

            if engagement is not None:
                try:
                    from wth_ai.state import observation_from_result

                    engagement.observe(
                        observation_from_result(result, role=f"autopilot:{step.kind}")
                    )
                except Exception:  # noqa: BLE001
                    pass
            if pairs is not None and step.evidence_ref:
                pairs.append({
                    "role": f"autopilot:{step.kind}",
                    "method": str(step.probe.get("method", "GET")).upper(),
                    "url": str(step.probe.get("url", "")),
                    "response_status": status,
                    "body_snippet": body[:500],
                    # GAP-008: fitur header (teknologi) untuk planner Bayesian.
                    "response_headers": {
                        str(key).lower(): str(value)
                        for key, value in (getattr(result, "response_headers", {}) or {}).items()
                    },
                })
            # Adaptif: kalau langkah ini diblokir WAF/rate/auth, susun mutasi
            # konkret dari katalog, jalankan lewat probe yang sama, ukur hasil,
            # lalu simpan strategi yang menang ke memori target.
            if adaptive and adaptive_left > 0 and step.probe.get("url"):
                try:
                    from wth_ai.adaptive import AdaptiveEngine, HttpRequest, classify

                    headers = getattr(result, "response_headers", {}) or {}
                    signal = classify(status, headers, body, analysis=getattr(result, "analysis", "") or "")
                    if signal.is_blocked:
                        engine = AdaptiveEngine(target)
                        seed = HttpRequest.from_probe(step.probe)
                        attempts = engine.retry_blocked(
                            seed, result, run_probe, limit=min(adaptive_moves, adaptive_left),
                            progress=None,
                        )
                        adaptive_left -= len(attempts)
                        for attempt in attempts:
                            bypass = AutopilotStep(
                                kind="adaptive",
                                title=f"{step.title} → {attempt.strategy}",
                                probe={
                                    "method": attempt.method,
                                    "url": attempt.url,
                                    # Auxiliary probe mewarisi sifat destruktif
                                    # operasi induknya: bypass tidak boleh
                                    # dianggap aman hanya karena URL-nya sama.
                                    "destructive": destructive,
                                },
                                ran=True, status=attempt.status,
                                changed=attempt.improved,
                                evidence_ref=attempt.transcript_id,
                                outcome=(
                                    StepOutcome.UNKNOWN.value if destructive
                                    else (
                                        StepOutcome.COMPLETED.value if attempt.status
                                        else StepOutcome.FAILED.value
                                    )
                                ),
                                note=(
                                    f"{attempt.description}; signal={attempt.signal}"
                                    f"{'; BYPASS' if attempt.improved else ''}"
                                    f"{'; ' + attempt.note if attempt.note else ''}"
                                ),
                            )
                            bypass.operation_id = (
                                f"{probe_operation_id(bypass.probe)}"
                                f"#adaptive:{attempt.strategy}"
                            )
                            report.steps.append(bypass)
                            if attempt.improved:
                                step.note = (step.note + f" | bypass: {attempt.strategy}").strip(" |")
                                step.changed = True
                            if pairs is not None and attempt.transcript_id:
                                pairs.append({
                                    "role": "autopilot:adaptive",
                                    "method": attempt.method,
                                    "url": attempt.url,
                                    "response_status": attempt.status,
                                    "body_snippet": "",
                                    "strategy": attempt.strategy,
                                    "improved": attempt.improved,
                                })
                            if learn is not None:
                                try:
                                    learn(bypass)
                                except Exception:  # noqa: BLE001
                                    pass
                except Exception:  # noqa: BLE001
                    pass
            if learn is not None:
                try:
                    learn(step)
                except Exception:  # noqa: BLE001
                    pass

        if stop_requested:
            break

        # Adaptive retries are auxiliary evidence, not a replacement for the
        # next cycle's re-proposal. Counting them toward the step budget starved
        # the loop after the first blocked request, so the engine never got to
        # act on the new 403 signal it had just observed.
        # Persist progress AFTER the cycle's steps have been recorded, so a
        # crash between cycles resumes at the next one instead of repeating.
        _save_checkpoint(cycle)

        primary_steps = sum(1 for s in report.steps if s.kind != "adaptive")
        if max_cycles > 0 and max_steps_per_cycle > 0 and primary_steps >= max_cycles * max_steps_per_cycle:
            report.stopped_reason = "batas langkah tercapai"
            break
    else:
        report.stopped_reason = report.stopped_reason or "cycle selesai"

    if not report.stopped_reason:
        report.stopped_reason = "selesai"
    return report


def format_report(report: AutopilotReport) -> str:
    lines = [
        f"## Autopilot — {report.target}",
        f"cycle: {report.cycles} · langkah dijalankan: {report.ran} · "
        f"perubahan terdeteksi: {report.changed} · langkah adaptif: {report.adaptive} · "
        f"berhenti: {report.stopped_reason}",
    ]
    if report.destructive_used:
        lines.append("PERINGATAN: ada langkah destruktif yang dijalankan (engagement opt-in). "
                     "Semua tercatat di audit log — periksa pemulihan tiap langkah.")
    for index, step in enumerate(report.steps, 1):
        if not step.ran and step.blocked_reason:
            lines.append(f"{index}. [DIBLOKIR] {step.title} — {step.blocked_reason}")
            continue
        mark = "✓" if step.ran else "·"
        lines.append(f"{index}. {mark} {step.title} (status={step.status or '-'}"
                     f"{' tx=' + step.evidence_ref if step.evidence_ref else ''})")
        if step.note:
            lines.append(f"    {step.note}")
    if not report.steps:
        lines.append("(tidak ada langkah yang bisa dijalankan)")
    return "\n".join(lines)
