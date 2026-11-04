"""Laporan Bahasa Indonesia untuk pembaca manajemen (dan rujukan regulasi).

Dua bagian yang tidak ada di laporan teknis:
  * Ringkasan Pimpinan — satu paragraf yang bisa dibaca tanpa konteks teknis.
  * Rujukan regulasi — saran pemetaan, BUKAN pendapat hukum.

Berkas rujukan sengaja hanya memuat nama regulasi dan kategori, tanpa nomor
pasal, karena nomor pasal tidak bisa diverifikasi dari repo ini. Laporan
menyatakan hal itu secara eksplisit.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

BSSN_MAP_PATH = Path(__file__).resolve().parents[2] / "data" / "kb" / "bssn_map.json"

DEFAULT_REGULATION = "Peraturan BSSN Nomor 1 Tahun 2024 tentang Pengelolaan Insiden Siber"

DISCLAIMER = (
    "Pemetaan regulasi di bawah adalah saran awal untuk memulai diskusi dengan "
    "tim kepatuhan, bukan pendapat hukum. Nomor pasal tidak dicantumkan karena "
    "tidak dapat diverifikasi dari repo ini."
)

_CLASS_ORDER = ("critical", "high", "medium", "low", "info")


def load_map(path: Path | None = None) -> dict[str, str]:
    path = Path(path or BSSN_MAP_PATH)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _bullets(rows: list[str], empty: str) -> str:
    return "\n".join(f"- {row}" for row in rows) if rows else f"- {empty}"


def _counts(findings) -> dict[str, int]:
    counts = {key: 0 for key in _CLASS_ORDER}
    for finding in findings:
        severity = _value(getattr(finding, "severity", "medium")).lower()
        counts[severity if severity in counts else "medium"] += 1
    return counts


def _value(value) -> str:
    value = getattr(value, "value", value)
    return "" if value is None else str(value)


def _label(finding) -> str:
    state = _value(getattr(finding, "state", "candidate"))
    if state == "confirmed":
        return "CONFIRMED"
    if state == "unproven":
        return "UNPROVEN"
    return "CANDIDATE"


def render_id_report(findings, *, run_meta: dict | None = None,
                     regulation_map: dict[str, str] | None = None) -> str:
    meta = run_meta or {}
    mapping = regulation_map if regulation_map is not None else load_map()
    counts = _counts(findings)
    total = len(findings)
    confirmed = sum(1 for finding in findings
                    if _value(getattr(finding, "state", "")) == "confirmed")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    summary_lines = [
        f"Total temuan: {total} (confirmed {confirmed}).",
        f"Sebaran severitas: kritis {counts['critical']}, tinggi {counts['high']}, "
        f"sedang {counts['medium']}, rendah {counts['low']}.",
    ]
    if meta.get("scan_mode"):
        summary_lines.append(f"Mode pemindaian: {meta['scan_mode']}.")
    if meta.get("status"):
        summary_lines.append(f"Status eksekusi: {meta['status']}.")

    table = ["| ID | Kelas | Severitas | Status | Target |",
             "|---|---|---|---|---|"]
    for finding in findings:
        table.append(
            f"| {getattr(finding, 'id', '-')} | {_value(getattr(finding, 'vuln_class', '-')) or '-'} "
            f"| {_value(getattr(finding, 'severity', '-')) or '-'} "
            f"| {_label(finding)} | {_value(getattr(finding, 'target', '-')) or '-'} |")

    regulation_rows = []
    for finding in findings:
        vuln_class = _value(getattr(finding, "vuln_class", "")) or "lainnya"
        regulation_rows.append(
            f"{getattr(finding, 'id', '-')}: {mapping.get(vuln_class, DEFAULT_REGULATION)}")
    regulation_rows = sorted(set(regulation_rows))

    confirmed_rows = [f"{getattr(finding, 'id', '-')}: "
                      f"{_value(getattr(finding, 'hypothesis', '')) or '-'}"
                      for finding in findings
                      if _value(getattr(finding, "state", "")) == "confirmed"]

    return "\n".join([
        "# Laporan Penindakan — Ringkasan Bahasa Indonesia",
        "",
        f"Dibuat: {stamp}",
        "",
        "## Ringkasan Pimpinan",
        "",
        " ".join(summary_lines),
        "",
        ("Temuan berstatus CONFIRMED sudah lolos penilaian berbasis bukti: "
         "request/response tertangkap, minimal satu reproduksi, dan kontrol "
         "negatif berperilaku berbeda. Temuan CANDIDATE belum memenuhi syarat "
         "itu dan tidak boleh dipakai untuk klaim di luar tim teknis."),
        "",
        "## Temuan",
        "",
        *table,
        "",
        "## Temuan Terkonfirmasi",
        "",
        _bullets(confirmed_rows, "tidak ada temuan terkonfirmasi pada run ini"),
        "",
        "## Rujukan Regulasi",
        "",
        _bullets(regulation_rows, "tidak ada temuan yang perlu dipetakan"),
        "",
        f"> {DISCLAIMER}",
        "",
        "## Langkah Berikutnya",
        "",
        "- Verifikasi ulang temuan CONFIRMED di lingkungan staging pemilik aset.",
        "- Terapkan perbaikan pada akar masalah, bukan pada gejala yang terlihat.",
        "- Jalankan ulang dengan `wth scan -n` untuk memastikan perbaikan bertahan.",
        "",
    ])


def write_id_report(findings, path: Path, **kwargs) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_id_report(findings, **kwargs), encoding="utf-8")
    return path