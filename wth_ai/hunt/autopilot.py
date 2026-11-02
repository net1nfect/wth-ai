"""Autopilot: jalankan langkah uji berbatas secara otonom, lalu belajar dari hasilnya.

Ini yang mengubah mesin proaktif dari "menyusun saran" menjadi "mengerjakan saran":
satu pass mengambil usulan dari `hunt/proactive.py` + payload berbatas dari
`hunt/payloads.py`, menjalankannya lewat seam `run_probe` (produksi: `http_probe`
dengan scope + audit + rate limit yang sudah ada), mencatat pasangan request/response
sebagai bukti, lalu MENYUSUN ULANG usulan dari apa yang baru dipelajari -- itu
perilaku adaptif, bukan daftar perintah tetap.

Yang TIDAK dilakukan autopilot:
* payload `blast_radius="mass"` (tidak pernah, walaupun destructive diizinkan);
* payload destruktif bila engagement belum membuka opt-in;
* menyentuh target di luar scope (gate tetap di `http_probe`);
* mengklaim temuan tanpa bukti -- hasil tiap langkah disimpan apa adanya, dan
  langkah tanpa perubahan dinyatakan negative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from wth_ai.hunt.payloads import Payload, filter_payloads, get_payload, render_probe
from wth_ai.hunt.proactive import NextTest, propose_next_tests

#: Seam produksi: menerima dict argumen `http_probe`, mengembalikan objek hasil
#: (dengan .stdout/.status_code/.transcript_id). Diuji dengan stub.
ProbeRunner = Callable[[dict[str, Any]], Any]


@dataclass
class AutopilotStep:
    kind: str            # "proactive" | "payload"
    title: str
    probe: dict[str, Any]
    ran: bool = False
    blocked_reason: str = ""
    status: int = 0
    changed: bool = False
    evidence_ref: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "title": self.title, "probe": self.probe, "ran": self.ran,
            "blocked_reason": self.blocked_reason, "status": self.status, "changed": self.changed,
            "evidence_ref": self.evidence_ref, "note": self.note,
        }


@dataclass
class AutopilotReport:
    target: str
    cycles: int = 0
    steps: list[AutopilotStep] = field(default_factory=list)
    stopped_reason: str = ""
    destructive_used: bool = False

    @property
    def ran(self) -> int:
        return sum(1 for step in self.steps if step.ran)

    @property
    def changed(self) -> int:
        return sum(1 for step in self.steps if step.changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target, "cycles": self.cycles, "ran": self.ran,
            "changed": self.changed, "stopped_reason": self.stopped_reason,
            "destructive_used": self.destructive_used,
            "steps": [step.to_dict() for step in self.steps],
        }


def _payload_to_step(payload: Payload, values: Mapping[str, Any]) -> AutopilotStep:
    probe = render_probe(payload, **values)
    return AutopilotStep(
        kind="payload",
        title=f"{payload.id} {payload.title}",
        probe={**probe, "payload_id": payload.id, "purpose": payload.title,
               "destructive": payload.requires_destructive},
        note=payload.cleanup,
    )


def _next_to_step(test: NextTest) -> AutopilotStep:
    return AutopilotStep(kind="proactive", title=test.title, probe=dict(test.probe),
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
) -> AutopilotReport:
    """Jalankan sampai `max_cycles` pass adaptif.

    Setiap cycle: susun kandidat (usulan proaktif + payload berbatas) -> jalankan ->
    catat bukti -> susun ulang dari hasil. Berhenti bila tidak ada kandidat baru atau
    langkah habis.
    """
    if destructive_allowed is None:
        destructive_allowed = bool(getattr(getattr(state, "scope", None), "destructive_allowed", False))

    report = AutopilotReport(target=target)
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

    values = {
        "base": target.rstrip("/"),
        "marker": "wth-auto-9z8y7",
        "own_id": "1",
        "own_other_id": "1",
        "other_id": "2",
        "own_email": "wth-test@example.invalid",
        "bucket": "wth-test-bucket-9z8y7",
        "none_jwt": "eyJhbGciOiJub25lIn0.e30.",
        "confusion_jwt": "wth-confusion-token",
    }

    attempted: set[tuple[str, str]] = set()

    for cycle in range(1, max(1, max_cycles) + 1):
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
            if payload.blast_radius == "mass" or not payload.auto_runnable:
                candidates.append(AutopilotStep(
                    kind="payload", title=f"{payload.id} {payload.title}", probe={}, ran=False,
                    blocked_reason=(
                        "payload berdampak massal — tidak pernah dijalankan mesin. "
                        f"Pakai padanan berbatas: {payload.bounded_alternative or 'lihat katalog'}"
                    ),
                ))
                continue
            if payload.requires_destructive and not destructive_allowed:
                candidates.append(AutopilotStep(
                    kind="payload", title=f"{payload.id} {payload.title}", probe={}, ran=False,
                    blocked_reason="payload destruktif tapi engagement belum membuka opt-in",
                ))
                continue
            candidates.append(_payload_to_step(payload, values))

        # (b) usulan proaktif (temuan + sinyal respons)
        for test in propose_next_tests(findings=findings, request_pairs=pairs,
                                      limit=max_steps_per_cycle):
            candidates.append(_next_to_step(test))

        # (c) payload berbatas yang relevan dengan kelas yang diminta
        for payload in filter_payloads(vuln_class, max_blast="single-object",
                                       include_destructive=destructive_allowed):
            if payload.blast_radius == "mass" or not payload.auto_runnable:
                continue  # dua guard yang sama, jalur otomatis
            candidates.append(_payload_to_step(payload, values))

        fresh = [c for c in candidates
                 if (str(c.probe.get("method", "")).upper(), str(c.probe.get("url", ""))) not in attempted]
        if not fresh:
            report.stopped_reason = "tidak ada kandidat baru (semua sudah dicoba)"
            break

        for step in fresh[: max(1, max_steps_per_cycle)]:
            key = (str(step.probe.get("method", "")).upper(), str(step.probe.get("url", "")))
            attempted.add(key)
            if not step.probe or not step.probe.get("url"):
                report.steps.append(step)
                continue
            if destructive_allowed and step.probe.get("destructive"):
                report.destructive_used = True
            if dry_run:
                step.note = (step.note + " | dry-run: tidak dijalankan").strip(" |")
                report.steps.append(step)
                continue
            try:
                result = run_probe(step.probe)
            except Exception as exc:  # noqa: BLE001
                step.note = f"gagal dijalankan: {type(exc).__name__}"
                report.steps.append(step)
                continue
            step.ran = True
            status = int(getattr(result, "status_code", 0) or 0)
            step.status = status
            step.evidence_ref = str(getattr(result, "transcript_id", "") or "")
            body = str(getattr(result, "stdout", "") or "")
            if status and status >= 500:
                step.note = (step.note + f" | status={status} (perhatikan)").strip(" |")
            if pairs is not None and step.evidence_ref:
                pairs.append({
                    "role": f"autopilot:{step.kind}",
                    "method": str(step.probe.get("method", "GET")).upper(),
                    "url": str(step.probe.get("url", "")),
                    "response_status": status,
                    "body_snippet": body[:500],
                })
            if learn is not None:
                try:
                    learn(step)
                except Exception:  # noqa: BLE001
                    pass
            report.steps.append(step)

        if len(report.steps) >= max_cycles * max_steps_per_cycle:
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
        f"perubahan terdeteksi: {report.changed} · berhenti: {report.stopped_reason}",
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
