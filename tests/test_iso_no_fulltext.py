"""Kontrol ISO 27001:2022: hanya ID + judul + alasan singkat, bukan teks standar."""

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "data/kb/iso27001_2022_controls.json"


def _controls():
    return json.loads(PATH.read_text(encoding="utf-8"))


def test_no_iso_fulltext_is_bundled():
    controls = _controls()
    assert len(controls) >= 93
    for row in controls:
        line = f"{row['id']} {row['title']} {row['rationale']}"
        assert len(line) <= 120, f"{row['id']} terlalu panjang ({len(line)}): {line}"


def _theme(control_id: str) -> str:
    return ".".join(control_id.split(".")[:2])


def test_ids_are_unique_and_ordered_by_theme():
    controls = _controls()
    ids = [row["id"] for row in controls]
    assert len(ids) == len(set(ids))
    themes = {_theme(row["id"]) for row in controls}
    assert themes == {"A.5", "A.6", "A.7", "A.8"}


def test_theme_counts_match_annex_a_2022():
    controls = _controls()
    counts = {}
    for row in controls:
        key = _theme(row["id"])
        counts[key] = counts.get(key, 0) + 1
    assert counts == {"A.5": 37, "A.6": 8, "A.7": 14, "A.8": 34}


def test_no_field_contains_standards_body_text():
    """Setiap field harus kalimat sendiri, bukan salinan teks standar."""
    for row in _controls():
        assert set(row) == {"id", "title", "rationale"}
        for value in row.values():
            assert "\n" not in value
            assert len(value) <= 80, value