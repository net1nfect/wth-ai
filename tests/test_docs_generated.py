"""P2-7: `docs/MODES.md` says "di-generate dari `wth_ai/modes.py`".

Before this, nothing generated it. The claim was false and the table was
hand-copied, so it could drift from `matrix_rows()` without any test noticing.
The generator is now the source of truth and `--check` is the gate.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gen_modes_docs.py"
DOC = ROOT / "docs" / "MODES.md"


def _load():
    spec = importlib.util.spec_from_file_location("gen_modes_docs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generator_exists():
    assert SCRIPT.is_file(), "scripts/gen_modes_docs.py belum ada"


def test_the_committed_table_matches_the_code():
    mod = _load()
    assert mod.render_table() in DOC.read_text(), (
        "tabel docs/MODES.md tidak sama dengan matrix_rows(); "
        "jalankan `python scripts/gen_modes_docs.py`"
    )


def test_render_table_has_one_row_per_mode():
    from wth_ai.modes import MODES, capability_header

    mod = _load()
    lines = [ln for ln in mod.render_table().splitlines() if ln.startswith("|")]
    assert len(lines) == len(MODES) + 2, "header + separator + satu baris per mode"
    for cell in capability_header():
        assert cell in lines[0]


def test_check_exits_zero_on_a_synced_file_and_nonzero_on_drift(tmp_path):
    synced = tmp_path / "synced.md"
    synced.write_text(DOC.read_text())
    ok = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--path", str(synced)],
        capture_output=True, text=True, timeout=60,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr

    drifted = tmp_path / "drifted.md"
    drifted.write_text(DOC.read_text().replace("| ◇ plan |", "| ◇ planx |"))
    bad = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--path", str(drifted)],
        capture_output=True, text=True, timeout=60,
    )
    assert bad.returncode != 0, "drift tidak terdeteksi"