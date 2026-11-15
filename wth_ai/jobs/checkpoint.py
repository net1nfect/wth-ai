"""Checkpoint schema umum — engine-agnostic recovery contract.

Desain (lihat ADR-015):
* `CheckpointEnvelope` menyimpan identitas eksekusi, versi skema, cursor,
  status per-langkah, dan metadata pemulihan.
* Ini BUKAN format khusus autopilot. Payload spesifik engine disimpan di
  `engine_state` (JSON) dan divalidasi oleh adapter masing-masing.
* Tidak ada secret/kredensial yang disimpan: hanya key langkah, status, dan
  referensi artefak (id, bukan isi).

Klasifikasi status sengaja eksplisit:
    completed  -> benar-benar selesai & terverifikasi; aman dilewati
    failed     -> dicoba, gagal; boleh diulang
    inflight   -> mulai tapi tidak ada bukti selesai; JANGAN dianggap sukses
    pending    -> belum dicoba
    unknown    -> berasal dari checkpoint lama; tidak boleh dianggap selesai
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

#: Naikkan saat struktur berubah secara tidak kompatibel.
CHECKPOINT_SCHEMA_VERSION = 2

#: Adapter payload per engine. Engine WAJIB terdaftar di sini untuk boleh
#: menulis checkpoint: tanpa validator, `engine_state` tidak bisa diperiksa saat
#: dimuat, sehingga checkpoint bisa diterima dalam bentuk yang tidak dipahami
#: engine-nya. Menambah engine = menambah validator, bukan sekadar nama.
def _validate_autopilot_state(state: Mapping[str, Any]) -> None:
    """Validasi payload autopilot: riwayat langkah harus berbentuk list of dict.

    Checkpoint dengan `steps` rusak tidak boleh diterima lalu dipakai untuk
    resume: hasilnya bisa "tidak ada langkah tersisa" padahal riwayatnya rusak.
    """
    steps = state.get("steps")
    if steps is None:
        return
    if not isinstance(steps, list):
        raise CheckpointValidationError("engine_state['steps'] harus list")
    for index, item in enumerate(steps):
        if not isinstance(item, Mapping):
            raise CheckpointValidationError(
                f"engine_state['steps'][{index}] harus mapping"
            )
        probe = item.get("probe")
        if probe is not None and not isinstance(probe, Mapping):
            raise CheckpointValidationError(
                f"engine_state['steps'][{index}]['probe'] harus mapping"
            )
        operation_id = item.get("operation_id")
        if operation_id is not None and not isinstance(operation_id, str):
            raise CheckpointValidationError(
                f"engine_state['steps'][{index}]['operation_id'] harus string"
            )
        ran = item.get("ran")
        if ran is not None and not isinstance(ran, bool):
            raise CheckpointValidationError(
                f"engine_state['steps'][{index}]['ran'] harus bool"
            )
        status = item.get("status")
        if status is not None and (
            not isinstance(status, int)
            or isinstance(status, bool)
            or status < 0
        ):
            raise CheckpointValidationError(
                f"engine_state['steps'][{index}]['status'] harus int >= 0"
            )
        outcome = item.get("outcome")
        if outcome not in (None, ""):
            try:
                StepOutcome(str(outcome))
            except ValueError as exc:
                raise CheckpointValidationError(
                    f"engine_state['steps'][{index}]['outcome'] tidak dikenal: "
                    f"{outcome!r}"
                ) from exc


def _validate_recon_state(state: Mapping[str, Any]) -> None:
    """Validasi payload engine recon (checkpoint per-tool).

    Bentuk yang diterima, dan alasannya:

    * `profile` — string; resume dengan profil berbeda boleh, tapi harus
      terlihat, jadi disimpan (bukan dicek sama di sini).
    * `tools` — mapping nama-tool -> {`outcome`, `run`}:
      - `outcome` wajib salah satu `StepOutcome`; hanya `COMPLETED` yang
        benar-benar dilewati saat resume.
      - `run` (hasil `ToolRun.to_dict()`) wajib ada saat `COMPLETED`: tanpa
        hasilnya, "melewati tool" berarti menghapus data yang sudah
        dikumpulkan, bukan melanjutkan.
    * `finished` — daftar nama tool yang benar-benar selesai (turunan
      `tools`, disimpan supaya bentuknya eksplisit saat dibaca manusia).
    """
    tools = state.get("tools", {})
    if tools is None:
        tools = {}
    if not isinstance(tools, Mapping):
        raise CheckpointValidationError("recon engine_state['tools'] harus mapping")
    for name, record in tools.items():
        if not isinstance(name, str) or not name:
            raise CheckpointValidationError("nama tool pada engine_state harus string")
        if not isinstance(record, Mapping):
            raise CheckpointValidationError(
                f"recon engine_state['tools'][{name!r}] harus mapping"
            )
        outcome = record.get("outcome")
        if outcome in (None, ""):
            raise CheckpointValidationError(
                f"recon engine_state['tools'][{name!r}] tanpa outcome"
            )
        try:
            parsed = StepOutcome(str(outcome))
        except ValueError as exc:
            raise CheckpointValidationError(
                f"recon engine_state['tools'][{name!r}] outcome tidak dikenal: {outcome!r}"
            ) from exc
        run = record.get("run")
        if parsed is StepOutcome.COMPLETED:
            if not isinstance(run, Mapping):
                raise CheckpointValidationError(
                    f"tool {name!r} COMPLETED tanpa hasil `run`; resume akan "
                    "kehilangan data yang sudah dikumpulkan"
                )
            items = run.get("normalized_items")
            if items is not None and not isinstance(items, list):
                raise CheckpointValidationError(
                    f"tool {name!r} `normalized_items` harus list"
                )
        elif run is not None and not isinstance(run, Mapping):
            raise CheckpointValidationError(
                f"tool {name!r} `run` harus mapping bila ada"
            )
    finished = state.get("finished", [])
    if finished is None:
        finished = []
    if not isinstance(finished, list) or not all(isinstance(x, str) for x in finished):
        raise CheckpointValidationError("recon engine_state['finished'] harus list[str]")
    profile = state.get("profile", "")
    if not isinstance(profile, str):
        raise CheckpointValidationError("recon engine_state['profile'] harus string")


ENGINE_VALIDATORS: dict[str, Any] = {
    "autopilot": _validate_autopilot_state,
    "recon": _validate_recon_state,
}

#: Engine yang benar-benar didukung (punya adapter). Worker/recon/executor
#: sengaja TIDAK ada di sini sampai adapter dan semantik resume-nya diuji;
#: mencantumkannya akan menjadi klaim dukungan yang tidak terbukti.
KNOWN_ENGINES: frozenset[str] = frozenset(ENGINE_VALIDATORS)


class CheckpointValidationError(ValueError):
    """Checkpoint tidak valid; pemanggil harus memperlakukannya sebagai absen."""


class StepOutcome(str, Enum):
    PENDING = "pending"
    INFLIGHT = "inflight"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


#: Transisi status yang diizinkan. Tujuannya bukan birokrasi: `COMPLETED`
#: yang sudah terverifikasi tidak boleh diam-diam kembali menjadi `INFLIGHT`
#: hanya karena sebuah retry kebetulan memakai key yang sama. Recovery yang
#: memang perlu menguji ulang langkah completed harus memakai `reopen()`
#: secara eksplisit (dan alasannya tercatat di `note`).
_ALLOWED_TRANSITIONS: dict[StepOutcome, frozenset[StepOutcome]] = {
    StepOutcome.PENDING: frozenset({
        StepOutcome.PENDING, StepOutcome.INFLIGHT, StepOutcome.COMPLETED,
        StepOutcome.FAILED, StepOutcome.UNKNOWN,
    }),
    StepOutcome.INFLIGHT: frozenset({
        StepOutcome.INFLIGHT, StepOutcome.COMPLETED, StepOutcome.FAILED,
        StepOutcome.UNKNOWN,
    }),
    StepOutcome.FAILED: frozenset({
        StepOutcome.FAILED, StepOutcome.INFLIGHT, StepOutcome.COMPLETED,
        StepOutcome.UNKNOWN,
    }),
    StepOutcome.UNKNOWN: frozenset({
        StepOutcome.UNKNOWN, StepOutcome.INFLIGHT, StepOutcome.COMPLETED,
        StepOutcome.FAILED,
    }),
    StepOutcome.COMPLETED: frozenset({StepOutcome.COMPLETED}),
}


def make_step_key(method: str, url: str) -> str:
    """Key request stabil untuk sebuah langkah HTTP.

    Key ini TIDAK cukup untuk membedakan dua operasi berbeda yang memakai
    method+URL sama (mis. BL-001 dan BL-002 sama-sama `POST /api/cart`).
    Identitas operasi yang sebenarnya ada di `StepRecord.operation_id`; key
    dipertahankan untuk kompatibilitas checkpoint lama dan tampilan.
    """
    return f"{str(method).upper()} {url}"


def _validate_resource_budget(budget: Mapping[str, int]) -> None:
    if not isinstance(budget, Mapping):
        raise CheckpointValidationError("resource_budget harus mapping")
    for key, value in budget.items():
        if not isinstance(key, str):
            raise CheckpointValidationError("resource_budget key harus string")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise CheckpointValidationError(f"resource_budget[{key!r}] harus int >= 0")


@dataclass
class StepRecord:
    key: str
    outcome: StepOutcome = StepOutcome.PENDING
    #: `verified` hanya bermakna untuk COMPLETED. Step completed tanpa
    #: verifikasi tidak boleh dilewati.
    verified: bool = False
    attempts: int = 0
    status: int = 0
    evidence_ref: str = ""
    note: str = ""
    #: Identitas operasi yang stabil dan LEBIH SPESIFIK dari `key`.
    #: Checkpoint lama tidak punya field ini; nilainya kosong dan pemanggil
    #: harus fallback ke `key` (lihat `identity`).
    operation_id: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            raise CheckpointValidationError("StepRecord.key wajib diisi")
        if not isinstance(self.operation_id, str):
            raise CheckpointValidationError("StepRecord.operation_id harus string")
        if not isinstance(self.outcome, StepOutcome):
            try:
                self.outcome = StepOutcome(str(self.outcome))
            except ValueError as exc:
                raise CheckpointValidationError(
                    f"outcome tidak dikenal: {self.outcome!r}"
                ) from exc
        if (
            not isinstance(self.attempts, int)
            or isinstance(self.attempts, bool)
            or self.attempts < 0
        ):
            raise CheckpointValidationError("attempts harus int >= 0")

    @property
    def identity(self) -> str:
        """Identitas operasi; fallback ke key untuk record legacy."""
        return self.operation_id or self.key

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "operation_id": self.operation_id,
            "outcome": self.outcome.value,
            "verified": self.verified,
            "attempts": self.attempts,
            "status": self.status,
            "evidence_ref": self.evidence_ref,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StepRecord":
        if not isinstance(data, Mapping) or not data.get("key"):
            raise CheckpointValidationError("StepRecord butuh `key`")
        if not isinstance(data["key"], str):
            raise CheckpointValidationError("StepRecord.key harus string")
        try:
            attempts = int(data.get("attempts", 0) or 0)
            status = int(data.get("status", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise CheckpointValidationError(
                "StepRecord.attempts/status harus angka"
            ) from exc
        operation_id = data.get("operation_id", "")
        if operation_id is None:
            operation_id = ""
        if not isinstance(operation_id, str):
            raise CheckpointValidationError("StepRecord.operation_id harus string")
        return cls(
            key=data["key"],
            operation_id=operation_id,
            outcome=data.get("outcome", StepOutcome.UNKNOWN.value),
            verified=bool(data.get("verified", False)),
            attempts=attempts,
            status=status,
            evidence_ref=str(data.get("evidence_ref", "") or ""),
            note=str(data.get("note", "") or ""),
        )


@dataclass
class CheckpointEnvelope:
    engine: str
    target: str
    cursor: int = 0
    steps: list[StepRecord] = field(default_factory=list)
    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    #: Anggaran sumber daya saat checkpoint dibuat (mis. max_cycles,
    #: max_steps_per_cycle). 0 berarti unbounded. Dipakai untuk mendeteksi
    #: resume dengan limit berbeda; bukan pengganti validasi cursor.
    resource_budget: dict[str, int] = field(default_factory=dict)
    #: Payload spesifik engine. Harus JSON-serializable dan tanpa secret.
    engine_state: dict[str, Any] = field(default_factory=dict)
    #: Fencing token / penanda kepemilikan checkpoint.
    #:
    #: Masalah yang diselesaikan: dua worker bisa memegang job yang sama
    #: (mis. worker A macet lebih lama dari lease-nya, operator me-retry, atau
    #: dua proses memakai `WTH_HOME` yang sama). Keduanya menulis checkpoint
    #: dengan `os.replace` dan *last writer wins* — checkpoint yang berisi
    #: temuan worker B bisa hilang diam-diam karena A menulis snapshot basi.
    #: `run_id` mengidentifikasi siapa yang menulis; `revision` naik setiap
    #: penulisan sukses. Penulis dengan `revision` lebih kecil dari yang sudah
    #: tersimpan DITOLAK (CAS). Kosong/0 = checkpoint lama; tetap bisa dibaca,
    #: dan penulis pertama boleh mengambil alih (lihat `save_checkpoint_cas`).
    run_id: str = ""
    revision: int = 0
    #: Waktu checkpoint dibuat (ISO-8601 UTC). Dipakai untuk kebijakan umur
    #: checkpoint: state lama bisa menyesatkan bila target sudah berubah, jadi
    #: operator boleh menetapkan batas umur. Kosong = tidak diketahui (checkpoint
    #: lama) dan TIDAK dianggap kedaluwarsa -- menebak umur lebih berbahaya
    #: daripada mengakuinya tidak diketahui.
    saved_at: str = ""

    def __post_init__(self) -> None:
        if self.engine not in KNOWN_ENGINES:
            raise CheckpointValidationError(f"engine tidak dikenal: {self.engine!r}")
        if not isinstance(self.target, str) or not self.target.strip():
            raise CheckpointValidationError("target wajib diisi")
        if not isinstance(self.cursor, int) or isinstance(self.cursor, bool):
            raise CheckpointValidationError("cursor harus int")
        if self.cursor < 0:
            raise CheckpointValidationError("cursor tidak boleh negatif")
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointValidationError(
                f"schema_version {self.schema_version} tidak didukung "
                f"(harapan {CHECKPOINT_SCHEMA_VERSION})"
            )
        if not isinstance(self.run_id, str):
            raise CheckpointValidationError("run_id harus string")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool):
            raise CheckpointValidationError("revision harus int")
        if self.revision < 0:
            raise CheckpointValidationError("revision tidak boleh negatif")
        if not isinstance(self.saved_at, str):
            raise CheckpointValidationError("saved_at harus string")
        _validate_resource_budget(self.resource_budget)
        self.validate_engine_state()

    # -- validasi & serialisasi -------------------------------------------

    def validate_engine_state(self) -> None:
        """Jalankan adapter engine pada `engine_state`.

        Dipanggil saat konstruksi, saat serialisasi, dan saat deserialisasi:
        checkpoint yang tidak lolos validasi tidak boleh ditulis maupun
        dipercaya untuk resume.
        """
        validator = ENGINE_VALIDATORS.get(self.engine)
        if validator is None:
            raise CheckpointValidationError(f"engine tidak dikenal: {self.engine!r}")
        validator(self.engine_state)

    def to_dict(self) -> dict[str, Any]:
        _validate_resource_budget(self.resource_budget)
        self.validate_engine_state()
        return {
            "schema_version": self.schema_version,
            "engine": self.engine,
            "target": self.target,
            "cursor": self.cursor,
            "steps": [s.to_dict() for s in self.steps],
            "resource_budget": dict(self.resource_budget),
            "engine_state": self.engine_state,
            "run_id": self.run_id,
            "revision": self.revision,
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckpointEnvelope":
        if not isinstance(data, Mapping):
            raise CheckpointValidationError(
                f"checkpoint harus mapping, bukan {type(data).__name__}"
            )
        if "schema_version" not in data:
            raise CheckpointValidationError("checkpoint tanpa schema_version")
        version = data.get("schema_version")
        if version != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointValidationError(
                f"schema_version {version} tidak didukung (harapan {CHECKPOINT_SCHEMA_VERSION})"
            )
        raw_steps = data.get("steps", [])
        if not isinstance(raw_steps, list):
            raise CheckpointValidationError("steps harus list")
        engine_state = data.get("engine_state", {})
        if not isinstance(engine_state, Mapping):
            raise CheckpointValidationError("engine_state harus mapping")
        raw_budget = data.get("resource_budget", {})
        if raw_budget is None:
            raw_budget = {}
        if not isinstance(raw_budget, Mapping):
            raise CheckpointValidationError("resource_budget harus mapping")
        env = cls(
            engine=data.get("engine", ""),
            target=data.get("target", ""),
            cursor=data.get("cursor", 0),
            steps=[StepRecord.from_dict(s) for s in raw_steps],
            schema_version=version,
            resource_budget=dict(raw_budget),
            engine_state=dict(engine_state),
            run_id=str(data.get("run_id", "") or ""),
            revision=data.get("revision", 0) or 0,
            saved_at=str(data.get("saved_at", "") or ""),
        )
        env.validate_engine_state()
        return env

    @classmethod
    def from_legacy(
        cls, data: Mapping[str, Any], *, engine: str,
    ) -> "CheckpointEnvelope":
        """Migrasi format lama (tanpa versi) secara konservatif.

        `attempted` lama hanya berarti "pernah dicoba", bukan "selesai". Semua
        entri dipetakan ke UNKNOWN + verified=False sehingga resume TIDAK
        melewatinya secara buta.
        """
        if not isinstance(data, Mapping):
            raise CheckpointValidationError("checkpoint legacy harus mapping")
        attempted = data.get("attempted") or []
        steps: list[StepRecord] = []
        for item in attempted:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                key = make_step_key(str(item[0]), str(item[1]))
                steps.append(StepRecord(key=key, outcome=StepOutcome.UNKNOWN))
        try:
            cursor = int(data.get("cycles_done", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise CheckpointValidationError(
                f"cycles_done tidak valid: {data.get('cycles_done')!r}"
            ) from exc
        return cls(
            engine=engine,
            target=str(data.get("target", "")),
            cursor=cursor,
            steps=steps,
        )

    def set_engine_state(self, state: Mapping[str, Any]) -> None:
        """Ganti payload engine dengan validasi (tidak ada assignment liar)."""
        candidate = dict(state)
        validator = ENGINE_VALIDATORS.get(self.engine)
        if validator is None:
            raise CheckpointValidationError(f"engine tidak dikenal: {self.engine!r}")
        validator(candidate)
        self.engine_state = candidate

    # -- query ------------------------------------------------------------

    def matches_target(self, target: str) -> bool:
        return self.target.rstrip("/") == str(target).rstrip("/")

    def age_seconds(self, *, now: datetime | None = None) -> float | None:
        """Umur checkpoint dalam detik, atau None bila `saved_at` tidak diketahui.

        Age adalah fakta, bukan kebijakan: modul ini tidak memutuskan berapa
        lama checkpoint "boleh" dipakai. Pemanggil yang memutuskan, supaya
        batasnya terlihat di tempat ia ditetapkan, bukan tersembunyi di sini.
        """
        if not self.saved_at:
            return None
        try:
            saved = datetime.fromisoformat(self.saved_at)
        except (TypeError, ValueError):
            return None
        if saved.tzinfo is None:
            saved = saved.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return max(0.0, (current - saved).total_seconds())

    def is_stale(self, max_age_seconds: float, *, now: datetime | None = None) -> bool:
        """True bila checkpoint LEBIH TUA dari `max_age_seconds`.

        `max_age_seconds <= 0` berarti tidak ada kebijakan umur (tidak pernah
        dianggap basi). Checkpoint tanpa `saved_at` juga tidak dianggap basi:
        umur yang tidak diketahui bukan alasan untuk membuang state.
        """
        if max_age_seconds is None or max_age_seconds <= 0:
            return False
        age = self.age_seconds(now=now)
        if age is None:
            return False
        return age > max_age_seconds

    def step(self, key: str) -> StepRecord | None:
        for s in self.steps:
            if s.key == key:
                return s
        return None

    def step_by_identity(self, operation_id: str) -> StepRecord | None:
        """Cari berdasarkan identitas operasi (bukan sekadar method+URL).

        Record legacy tanpa `operation_id` tidak pernah cocok dengan pencarian
        identitas; pemanggil memakai `step(key)` sebagai fallback.
        """
        if not operation_id:
            return None
        for s in self.steps:
            if s.operation_id == operation_id:
                return s
        return None

    def should_skip(self, key: str) -> bool:
        """Hanya COMPLETED yang terverifikasi boleh dilewati."""
        rec = self.step(key)
        return bool(rec and rec.outcome == StepOutcome.COMPLETED and rec.verified)

    def should_skip_identity(self, operation_id: str) -> bool:
        rec = self.step_by_identity(operation_id)
        return bool(rec and rec.outcome == StepOutcome.COMPLETED and rec.verified)

    def retryable(self, key: str) -> bool:
        rec = self.step(key)
        if rec is None:
            return True
        return rec.outcome in (StepOutcome.FAILED, StepOutcome.UNKNOWN)

    def completed_keys(self) -> set[str]:
        return {s.key for s in self.steps if s.outcome == StepOutcome.COMPLETED and s.verified}

    def completed_operations(self) -> set[str]:
        """Identitas operasi completed+verified (record baru)."""
        return {
            s.operation_id for s in self.steps
            if s.operation_id
            and s.outcome == StepOutcome.COMPLETED
            and s.verified
        }

    def completed_legacy_keys(self) -> set[str]:
        """Key completed dari checkpoint lama yang belum punya operation_id."""
        return {
            s.key for s in self.steps
            if not s.operation_id
            and s.outcome == StepOutcome.COMPLETED
            and s.verified
        }

    def failed_keys(self) -> set[str]:
        return {s.key for s in self.steps if s.outcome == StepOutcome.FAILED}

    def inflight_keys(self) -> set[str]:
        return {s.key for s in self.steps if s.outcome == StepOutcome.INFLIGHT}

    # -- mutasi -----------------------------------------------------------

    def _find_for_mark(self, key: str, operation_id: str) -> StepRecord | None:
        """Cari record yang tepat untuk (key, operation_id).

        Kasus penting: dua operasi berbeda boleh berbagi `key` yang sama.
        Karena itu pencarian identitas didahulukan; pencarian key hanya
        mengikat record legacy yang belum punya identitas.
        """
        if operation_id:
            rec = self.step_by_identity(operation_id)
            if rec is not None:
                return rec
            rec = self.step(key)
            if rec is None:
                return None
            if not rec.operation_id:
                # Backfill checkpoint lama tanpa mengubah key tampilan.
                rec.operation_id = operation_id
                return rec
            if rec.operation_id != operation_id:
                # Key sama, tetapi record ini milik operasi lain.
                return None
        return self.step(key)

    def mark(
        self,
        key: str,
        outcome: StepOutcome,
        *,
        verified: bool = False,
        status: int = 0,
        evidence_ref: str = "",
        note: str = "",
        operation_id: str = "",
    ) -> StepRecord:
        """Upsert idempoten dengan validasi transisi.

        `key` tetap dipertahankan untuk kompatibilitas. Untuk operasi yang
        mungkin berbagi method+URL, kirim `operation_id`: dua operasi berbeda
        akan menjadi dua record, sedangkan resume memakai identitas ini.
        Transisi dari COMPLETED ke status lain ditolak; gunakan `reopen()`
        bila memang perlu menguji ulang secara eksplisit.
        """
        if not isinstance(outcome, StepOutcome):
            try:
                outcome = StepOutcome(str(outcome))
            except ValueError as exc:
                raise CheckpointValidationError(
                    f"outcome tidak dikenal: {outcome!r}"
                ) from exc
        rec = self._find_for_mark(key, operation_id)
        if rec is not None:
            if outcome not in _ALLOWED_TRANSITIONS[rec.outcome]:
                raise CheckpointValidationError(
                    f"transisi {rec.outcome.value} -> {outcome.value} ilegal untuk "
                    f"{rec.identity!r}; gunakan reopen() untuk recovery eksplisit"
                )
            if (
                rec.outcome == StepOutcome.COMPLETED
                and rec.verified
                and outcome == StepOutcome.COMPLETED
                and not verified
            ):
                raise CheckpointValidationError(
                    f"verified COMPLETED tidak boleh diturunkan menjadi unverified "
                    f"untuk {rec.identity!r}"
                )
        if rec is None:
            rec = StepRecord(key=key, operation_id=operation_id)
            self.steps.append(rec)
        elif operation_id and not rec.operation_id:
            rec.operation_id = operation_id
        rec.outcome = outcome
        rec.verified = bool(verified and outcome == StepOutcome.COMPLETED)
        if status:
            rec.status = int(status)
        if evidence_ref:
            rec.evidence_ref = evidence_ref
        if note:
            rec.note = note
        if outcome in (StepOutcome.FAILED, StepOutcome.COMPLETED, StepOutcome.UNKNOWN):
            rec.attempts += 1
        return rec

    def reopen(
        self,
        key: str,
        *,
        operation_id: str = "",
        reason: str = "",
    ) -> StepRecord:
        """Mekanisme recovery eksplisit: buka kembali langkah untuk diuji ulang.

        Hanya dipakai ketika bukti menunjukkan status sebelumnya tidak benar
        (mis. COMPLETED yang ternyata belum terjadi). `verified` dikosongkan
        dan `reason` disimpan supaya keputusan itu dapat diaudit.
        """
        rec = self._find_for_mark(key, operation_id)
        if rec is None:
            rec = StepRecord(key=key, operation_id=operation_id)
            self.steps.append(rec)
        rec.outcome = StepOutcome.INFLIGHT
        rec.verified = False
        if reason:
            rec.note = (
                f"{rec.note} | reopen: {reason}".strip(" |")
                if rec.note else f"reopen: {reason}"
            )
        return rec

    def mark_http(
        self,
        method: str,
        url: str,
        outcome: StepOutcome,
        **kwargs: Any,
    ) -> StepRecord:
        """Varian `mark()` untuk langkah HTTP; key dibentuk konsisten.

        Mencegah bug nyata: memanggil `mark(url)` langsung menghasilkan key yang
        tidak cocok dengan `make_step_key(method,url)` yang dipakai engine saat
        membandingkan `completed`.
        """
        return self.mark(make_step_key(method, url), outcome, **kwargs)
