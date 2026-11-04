"""Doctor melihat lapisan red team: mode, bukti, scope, advisory, KB, MCP."""

from wth_ai.tools.doctor import ToolDoctor


def _doctor(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    return ToolDoctor()


def test_redteam_checks_report_ok(tmp_path, monkeypatch):
    info = _doctor(tmp_path, monkeypatch).check_redteam()
    assert info["modes"] is True
    assert info["evidence"] is True, info["evidence_error"]
    assert info["scope_card"] is True
    assert info["disclosure"] is True
    assert info["skill_select"] is True
    assert info["mcp_gating"] is True


def test_missing_kb_is_warning_not_failure(tmp_path, monkeypatch):
    info = _doctor(tmp_path, monkeypatch).check_redteam()
    assert info["kb"] is False
    assert info["ok"] is True  # KB belum dibangun bukan kegagalan


def test_doctor_output_mentions_redteam_section(tmp_path, monkeypatch):
    report = _doctor(tmp_path, monkeypatch).full_report()
    assert "## Red Team Engine" in report
    assert "evidence roundtrip: OK" in report
    assert "KB: belum dibangun" in report