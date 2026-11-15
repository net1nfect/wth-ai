#!/usr/bin/env python3
"""Launcher tipis WTH-AI.

GAP-012: implementasi CLI tunggal ada di `wth_ai/main_cli.py` (ikut wheel
sebagai kode Python, bukan data). File ini hanya shim untuk source checkout,
Docker (`/app/wth-main.py`), PyInstaller, dan test yang memanggil path root.
Sebelumnya implementasi 1800+ baris tinggal di root dan TIDAK ikut wheel,
sehingga `pip install` mustahil berjalan (WU-208 / ARCH-02).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
os.chdir(_ROOT)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from wth_ai.main_cli import cli  # noqa: E402  (butuh cwd/sys.path di atas)

if __name__ == "__main__":
    cli()
