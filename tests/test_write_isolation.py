"""Penulis legacy menghormati env var sehingga test/CI tidak mengotori repo."""

import importlib
from pathlib import Path

from wth_ai.memory.engagement_memory import EngagementMemory


def test_engagement_memory_uses_env_dir(tmp_path, monkeypatch):
    target = tmp_path / "eng"
    monkeypatch.setenv("WTH_ENGAGEMENT_DIR", str(target))
    memory = EngagementMemory("sesi-x")
    memory.save()
    assert (target / "sesi-x" / "memory.json").is_file()


def test_report_generator_uses_env_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_REPORT_DIR", str(tmp_path / "reports"))
    import reporting.report_generator as module

    module = importlib.reload(module)
    generator = module.ReportGenerator()
    assert generator.output_dir == tmp_path / "reports"
    assert generator.output_dir.is_dir()


def test_logger_uses_env_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_LOG_DIR", str(tmp_path / "logs"))
    import core.logger as module

    module = importlib.reload(module)
    logger = module.get_logger("wth.test.isolasi", "sesi-log")
    handlers = [h for h in logger.handlers if hasattr(h, "baseFilename")]
    assert handlers
    assert str(tmp_path / "logs") in handlers[0].baseFilename
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_session_fixture_points_outside_repo(isolated_root):
    import os

    repo = Path(__file__).resolve().parents[1]
    for name in ("WTH_HOME", "WTH_ENGAGEMENT_DIR", "WTH_LOG_DIR", "WTH_REPORT_DIR"):
        value = Path(os.environ[name])
        assert value.is_absolute()
        assert not value.is_relative_to(repo), name