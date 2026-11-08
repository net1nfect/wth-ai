"""Agregat telemetry opt-in (§1.2 poin 5): angka, bukan konteks engagement.

Yang boleh keluar hanya empat hal: jumlah run, daftar durasi, hitungan exit
code, dan versi WTH-AI. Modul ini **tidak** punya jalur kirim: fungsi di sini
murni membentuk payload supaya isinya bisa diuji tanpa jaringan. Host, target,
temuan, dan secret tidak pernah dibaca, bukan sekadar tidak dikirim.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import Any


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _exit_code(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return str(value)

def _package_version() -> str:
    try:
        return version("wth-ai")
    except PackageNotFoundError:
        return "unknown"

def build_report(run_meta: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Bentuk payload telemetry dari metadata run; tidak pernah melempar."""
    durations: list[float] = []
    exit_codes: dict[str, int] = {}
    runs = 0

    for item in run_meta or []:
        if not isinstance(item, Mapping):
            continue
        runs += 1
        seconds = _seconds(item.get("duration_s"))
        if seconds is not None:
            durations.append(seconds)
        code = _exit_code(item.get("exit_code"))
        if code is not None:
            exit_codes[code] = exit_codes.get(code, 0) + 1

    # Empat kunci ini adalah seluruh kontrak payload: menambah field di sini
    # berarti mengirim sesuatu yang belum diputuskan untuk dikirim. Kontraknya
    # dijaga test (tests/test_telemetry_aggregate.py), bukan assert runtime.
    return {
        "runs": runs,
        "durations": durations,
        "exit_codes": exit_codes,
        "version": _package_version(),
    }
