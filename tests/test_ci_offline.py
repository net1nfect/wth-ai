"""CI dan mode offline: workflow valid, action valid, doctor melaporkan WTH_OFFLINE."""

from pathlib import Path

import yaml

from wth_ai.tools.doctor import ToolDoctor

REPO = Path(__file__).resolve().parents[1]


def test_workflow_is_valid_yaml_and_runs_pytest():
    data = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
    steps = data["jobs"]["test"]["steps"]
    text = " ".join(str(step.get("run", "")) for step in steps)
    assert "pytest tests/" in text
    assert "--offline-demo" in text
    assert "verify out/bundle" in text


def test_action_is_composite_with_bundle_output():
    data = yaml.safe_load((REPO / "action.yml").read_text())
    assert data["runs"]["using"] == "composite"
    assert "bundle" in data["outputs"]
    steps = data["runs"]["steps"]
    assert steps[0]["shell"] == "bash"


def test_offline_compose_has_internal_network():
    data = yaml.safe_load((REPO / "docker-compose.offline.yaml").read_text())
    assert data["networks"]["lab"]["internal"] is True
    assert data["services"]["wth"]["environment"]["WTH_OFFLINE"] == "1"


def test_doctor_reports_offline_line(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    monkeypatch.setenv("WTH_OFFLINE", "1")
    report = ToolDoctor().full_report()
    assert "offline: ya" in report


def test_doctor_reports_online_line(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    monkeypatch.delenv("WTH_OFFLINE", raising=False)
    assert "offline: tidak" in ToolDoctor().full_report()