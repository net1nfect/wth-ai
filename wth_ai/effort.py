"""Level effort (`/effort`) — satu tombol untuk budget generasi LLM.

Kenapa ada: `config.models.max_tokens` sudah lama ada tapi TIDAK punya
permukaan operator (hanya `profiles.py` yang menyetelnya, tersembunyi di
preset). Gejala terukur dari sesi operator: jawaban terpotong di tengah
kalimat / terasa terlalu pendek, tanpa cara menaikkan budget selain edit YAML.

Honest scope — yang kita kendalikan di sini:
* `max_tokens` keluaran,
* timeout per-role (planner/executor),
* reset cache provider supaya nilai baru BENAR-BENAR terpakai (instance
  provider di-cache dengan parameter config saat dibuat -- perubahan config
  tanpa reset tidak sampai ke sana).

Yang TIDAK diklaim: "kepintaran berpikir"/reasoning internal model itu
keputusan provider/server di balik endpoint; kalau provider membatasi output
di sisi mereka, effort tidak bisa menembusnya.
"""

from __future__ import annotations

from typing import Any

#: Level → parameter konkret. Nilai "medium" sengaja == default lama
#: (max_tokens 8192, timeout 90/120) supaya sesi tanpa /effort tidak berubah.
EFFORT_LEVELS: dict[str, dict[str, Any]] = {
    "low": {
        "max_tokens": 2048,
        "planner_timeout_seconds": 60,
        "executor_timeout_seconds": 90,
        "label": "hemat — jawaban ringkas",
    },
    "medium": {
        "max_tokens": 8192,
        "planner_timeout_seconds": 90,
        "executor_timeout_seconds": 120,
        "label": "default — seimbang",
    },
    "high": {
        "max_tokens": 16384,
        "planner_timeout_seconds": 135,
        "executor_timeout_seconds": 180,
        "label": "panjang — analisa lebih dalam",
    },
    "xhigh": {
        "max_tokens": 32768,
        "planner_timeout_seconds": 180,
        "executor_timeout_seconds": 240,
        "label": "berat — output & konteks besar",
    },
    "ultra": {
        "max_tokens": 65536,
        "planner_timeout_seconds": 270,
        "executor_timeout_seconds": 360,
        "label": "maksimum — pakai saat butuh",
    },
}

DEFAULT_EFFORT = "medium"


def effort_names() -> list[str]:
    return list(EFFORT_LEVELS)


def apply_effort(config: Any, level: str) -> dict[str, Any]:
    """Terapkan level ke config.models; kembalikan spec yang dipakai."""
    spec = EFFORT_LEVELS[level]
    config.models.max_tokens = int(spec["max_tokens"])
    config.models.planner_timeout_seconds = int(spec["planner_timeout_seconds"])
    config.models.executor_timeout_seconds = int(spec["executor_timeout_seconds"])
    return dict(spec)


def detect_effort(config: Any) -> str:
    """Tebak level dari max_tokens saat ini; "custom" kalau tidak persis cocok."""
    mt = int(getattr(getattr(config, "models", None), "max_tokens", 0) or 0)
    for name, spec in EFFORT_LEVELS.items():
        if spec["max_tokens"] == mt:
            return name
    return "custom"
