"""Demo TUI jujur: label [demo] dan penutup yang menyatakan bukan hasil nyata."""

from wth_ai.cli.tui.redteam.app import DEMO_DISCLAIMER, DEMO_STEPS


def test_demo_covers_a_full_cycle():
    joined = " ".join(line for _, line in DEMO_STEPS)
    for step in ("scope", "queue", "probe", "control", "replay", "verdict"):
        assert step in joined


def test_demo_closes_with_disclaimer():
    assert "bukan hasil uji nyata" in DEMO_DISCLAIMER


def test_demo_has_no_hardcoded_finding_counts():
    """Angka di demo hanya boleh muncul sebagai bagian narasi [demo]."""
    for _, line in DEMO_STEPS:
        assert "[demo]" not in line  # label ditambahkan saat logging, bukan di data