"""run.json: status, angka, dan bisa dibaca kembali."""

import json

from wth_ai.redteam.run_meta import (STATUS_COMPLETED, STATUS_STOPPED,
                                     RunMeta)


def test_writes_valid_json(tmp_path):
    path = RunMeta("RUN-1", targets=["172.28.0.10"], confirmed=1).write(
        tmp_path / "run.json")
    data = json.loads(path.read_text())
    assert data["run_id"] == "RUN-1"
    assert data["status"] == STATUS_COMPLETED
    assert data["targets"] == ["172.28.0.10"]
    assert data["started_at"] and data["finished_at"]


def test_stopped_is_not_failure(tmp_path):
    path = RunMeta("RUN-2", status=STATUS_STOPPED).write(tmp_path / "run.json")
    assert json.loads(path.read_text())["status"] == "stopped"


def test_round_trip(tmp_path):
    path = RunMeta("RUN-3", scan_mode="quick", cost_usd=0.5).write(
        tmp_path / "run.json")
    meta = RunMeta.load(path)
    assert meta.scan_mode == "quick"
    assert meta.cost_usd == 0.5