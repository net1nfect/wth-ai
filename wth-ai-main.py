"""Shim: satu-satunya sumber CLI ada di `wth_ai/cli/wth-ai-main.py`.

File ini dipertahankan supaya `python wth-ai-main.py` (dan dokumentasi lama)
tetap jalan, tetapi tidak boleh berisi logika. Salinan kedua pernah menyimpang:
root kehilangan `resolve_image_digest` pada `sandbox run --image` dan argumen
`chains=` pada `write_findings`, sehingga test menguji file yang bukan produk.
"""

from __future__ import annotations

import runpy
from pathlib import Path

_TARGET = Path(__file__).resolve().parent / "wth_ai" / "cli" / "wth-ai-main.py"

if not _TARGET.is_file():
    raise SystemExit(f"WTH-AI tidak menemukan sumber CLI di {_TARGET}")

runpy.run_path(str(_TARGET), run_name="__main__")
