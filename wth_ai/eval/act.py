"""Loop self-improvement yang BERTINDAK atas hasil pengukuran.

Sebelumnya pengukuran hanya melaporkan ("learning store kosong", "tidak ada
kegagalan tercatat") dan tidak mengubah apa pun. Sekarang hasil ukur itu dipakai:

1. **Bobot kelas ditulis** ke `$WTH_HOME/eval/weights.json`, dan `hunt/proactive.py`
   membacanya sehingga prioritas langkah uji berikutnya ikut berubah. Ukur -> ubah
   perilaku, bukan ukur -> lapor.
2. **Backfill**: kalau store learning berpihak satu arah (nol kegagalan) sementara
   FindingsDB menyimpan false positive, kegagalan itu DITULISKAN ke store. Tanpa ini
   `success_rate()` selamanya angka kemenangan, bukan presisi.

Prinsip yang dijaga: butuh sampel minimum sebelum mengubah bobot (dua percobaan bukan
bukti), dan setiap aksi menyebut alasannya di output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

#: Bobot dijepit di rentang ini: pengukuran kecil tidak boleh menggeser prioritas
#: ekstrem, dan bobot bukan klaim kualitas -- ia hanya urutan kerja.
MIN_WEIGHT = 0.5
MAX_WEIGHT = 2.0

#: Di bawah ini, tidak ada perubahan bobot (sampel terlalu kecil untuk berarti).
MIN_SAMPLES = 3


@dataclass
class EvalAction:
    kind: str          # weight | backfill | note
    detail: str
    applied: bool = False
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail, "applied": self.applied, "path": self.path}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_weights_path() -> Path | None:
    try:
        from wth_ai.workspace import wth_home

        return wth_home() / "eval" / "weights.json"
    except Exception:  # noqa: BLE001
        return None


def compute_weights(
    per_class: Mapping[str, Mapping[str, int]],
    *,
    base: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], list[str]]:
    """Hitung bobot dari (verified, false_positive) per kelas.

    (weights, notes). Kelas tanpa sampel cukup TIDAK diubah, dan itu dicatat -- supaya
    "tidak ada data" tidak pernah terlihat seperti "sudah diukur".
    """
    from wth_ai.hunt.proactive import DEFAULT_WEIGHTS

    weights = dict(base or DEFAULT_WEIGHTS)
    notes: list[str] = []
    for vuln_class, counts in sorted(per_class.items()):
        verified = int(counts.get("verified", 0) or 0)
        false_positive = int(counts.get("false_positive", 0) or 0)
        total = verified + false_positive
        current = float(weights.get(vuln_class, 1.0))
        if total < MIN_SAMPLES:
            notes.append(f"{vuln_class}: {total} sampel (<{MIN_SAMPLES}) — bobot tidak diubah")
            continue
        # presisi sederhana: bagian hasil yang terbukti benar, dinaikkan ke skala 0.5..2.0
        precision = verified / total
        proposed = 0.5 + 1.5 * precision
        proposed = max(MIN_WEIGHT, min(MAX_WEIGHT, proposed))
        weights[vuln_class] = round(proposed, 3)
        notes.append(
            f"{vuln_class}: verified={verified} false_positive={false_positive} "
            f"-> bobot {current:.2f} menjadi {proposed:.2f}"
        )
    return weights, notes


def collect_class_counts(
    learning: Any,
    findings_db: Any | None = None,
) -> dict[str, dict[str, int]]:
    """(verified, false_positive) per kelas dari store learning + FindingsDB."""
    counts: dict[str, dict[str, int]] = {}
    try:
        rows = learning.all_records() if hasattr(learning, "all_records") else []
    except Exception:  # noqa: BLE001
        rows = []
    for row in rows or []:
        vuln_class = str((row or {}).get("vuln_class") or "").lower()
        if not vuln_class:
            continue
        slot = counts.setdefault(vuln_class, {"verified": 0, "false_positive": 0})
        if (row or {}).get("failure") or str((row or {}).get("outcome", "")).lower() in {"failure", "false_positive"}:
            slot["false_positive"] += 1
        else:
            slot["verified"] += 1

    if findings_db is not None:
        try:
            for finding in findings_db.list_findings():
                state = getattr(finding, "state", "")
                state_value = getattr(state, "value", state)
                if str(state_value).lower() not in {"false_positive", "rejected"}:
                    continue
                vuln_class = str(getattr(finding, "vuln_class", "") or getattr(finding, "type", "")).lower()
                if not vuln_class:
                    continue
                slot = counts.setdefault(vuln_class, {"verified": 0, "false_positive": 0})
                slot["false_positive"] += 1
        except Exception:  # noqa: BLE001
            pass
    return counts


def backfill_failures(learning: Any, findings_db: Any) -> EvalAction:
    """Tuliskan false positive FindingsDB ke store learning bila belum tercatat.

    Ini menutup cacat dua-arah: kalau store hanya punya kemenangan, setiap angka
    presisi yang dihitung darinya adalah angka kemenangan.
    """
    written = 0
    try:
        for finding in findings_db.list_findings():
            state = getattr(finding, "state", "")
            state_value = getattr(state, "value", state)
            if str(state_value).lower() not in {"false_positive", "rejected"}:
                continue
            try:
                learning.record_from_finding(finding, verified=False)
                written += 1
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return EvalAction("backfill", "tidak bisa membaca FindingsDB", applied=False)
    detail = f"{written} false positive FindingsDB ditulis ke store learning"
    return EvalAction("backfill", detail, applied=written > 0)


def act_on_report(
    report: Any = None,
    *,
    learning: Any = None,
    findings_db: Any = None,
    weights_path: Path | None = None,
) -> list[EvalAction]:
    """Jalankan aksi nyata dari satu laporan eval (atau dari store langsung)."""
    from wth_ai.memory.learning import HuntMemoryLearning

    actions: list[EvalAction] = []
    learning = learning or HuntMemoryLearning()

    counts = collect_class_counts(learning, findings_db)
    previous = _load_existing_weights(weights_path) or {}
    weights, notes = compute_weights(counts, base=previous or None)
    target = weights_path or default_weights_path()
    # Menulis file hanya bila ADA yang berubah. Versi pertama menulis file "tidak ada
    # perubahan" dan menandainya applied=True -- itu laporan palsu: seolah sistem
    # bertindak padahal tidak ada sampel yang cukup untuk mengubah apa pun.
    changed = {k: v for k, v in weights.items() if abs(float(previous.get(k, 0.0)) - float(v)) > 1e-9}
    if target is not None and changed:
        try:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_text(
                json.dumps({"updated": _now(), "weights": weights, "notes": notes}, indent=2),
                encoding="utf-8",
            )
            actions.append(EvalAction("weight", "; ".join(notes) or "bobot diperbarui", applied=True,
                                      path=str(target)))
        except OSError as exc:
            actions.append(EvalAction("weight", f"gagal menulis bobot: {exc}", applied=False))
    else:
        actions.append(EvalAction("weight", "; ".join(notes) or "belum ada sampel cukup", applied=False))

    if findings_db is not None:
        actions.append(backfill_failures(learning, findings_db))

    if not any(a.applied for a in actions):
        actions.append(EvalAction("note", "belum ada sampel cukup untuk mengubah perilaku", applied=False))
    return actions


def _load_existing_weights(path: Path | None) -> dict[str, float] | None:
    from wth_ai.hunt.proactive import load_weights

    target = path or default_weights_path()
    return load_weights(target) if target else None


def format_actions(actions: Iterable[EvalAction]) -> str:
    lines = ["## Aksi self-improvement"]
    for action in actions:
        mark = "✓" if action.applied else "·"
        lines.append(f"{mark} [{action.kind}] {action.detail}")
        if action.path:
            lines.append(f"    file: {action.path}")
    if not any(a.applied for a in actions):
        lines.append("(tidak ada yang berubah — dan itu dinyatakan, bukan disembunyikan)")
    return "\n".join(lines)
