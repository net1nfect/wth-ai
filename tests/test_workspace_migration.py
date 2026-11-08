"""Migrasi nama lama → baru, sekali dan idempoten.

`wth-results/` dan `config/wth_config.yaml` sempat memakai nama lama. Operator
yang sudah punya workspace tidak boleh kehilangan riwayat pemindaiannya, tapi
kode setelah rename hanya tahu nama baru. `init_workspace()` jadi satu-satunya
hook yang sudah dipanggil installer, gateway, dan entrypoint Docker.
"""

from __future__ import annotations

from wth_ai.workspace import RESULTS_DIR_NAME, init_workspace


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("WTH_HOME", str(home))
    return home


def test_legacy_results_dir_is_renamed(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    legacy = home / "wth-results" / "job-1"
    legacy.mkdir(parents=True)
    (legacy / "result.json").write_text('{"ok": true}', encoding="utf-8")

    init_workspace(home)

    moved = home / RESULTS_DIR_NAME / "job-1" / "result.json"
    assert moved.read_text(encoding="utf-8") == '{"ok": true}'
    assert not (home / "wth-results").exists()


def test_migration_is_idempotent(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / "wth-results" / "job-1").mkdir(parents=True)
    (home / "wth-results" / "job-1" / "result.json").write_text("{}", encoding="utf-8")

    init_workspace(home)
    init_workspace(home)

    assert (home / RESULTS_DIR_NAME / "job-1" / "result.json").is_file()


def test_existing_new_dir_is_never_overwritten(tmp_path, monkeypatch):
    """Kalau kedua direktori ada, yang baru menang; yang lama dibiarkan."""
    home = _home(tmp_path, monkeypatch)
    (home / "wth-results" / "old").mkdir(parents=True)
    (home / "wth-results" / "old" / "result.json").write_text("lama", encoding="utf-8")
    (home / RESULTS_DIR_NAME / "new").mkdir(parents=True)
    (home / RESULTS_DIR_NAME / "new" / "result.json").write_text("baru", encoding="utf-8")

    init_workspace(home)

    assert (home / RESULTS_DIR_NAME / "new" / "result.json").read_text() == "baru"
    assert (home / "wth-results" / "old" / "result.json").is_file()


def test_legacy_config_file_is_renamed(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / "config").mkdir(parents=True)
    (home / "config" / "wth_config.yaml").write_text("results_dir: x\n", encoding="utf-8")

    init_workspace(home)

    assert not (home / "config" / "wth_config.yaml").exists()
    assert (home / "config" / "wth_ai_config.yaml").read_text() == "results_dir: x\n"


def test_migration_does_not_touch_tracked_engagement_dirs(tmp_path, monkeypatch):
    """AGENTS.md §5: data/engagements, logs, reports bukan milik migrasi."""
    home = _home(tmp_path, monkeypatch)
    for sub in ("data/engagements/e1", "logs/jobs", "reports"):
        (home / sub).mkdir(parents=True)
        (home / sub / "keep.txt").write_text("x", encoding="utf-8")

    init_workspace(home)

    for sub in ("data/engagements/e1", "logs/jobs", "reports"):
        assert (home / sub / "keep.txt").is_file(), sub