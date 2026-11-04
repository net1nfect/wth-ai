"""Novel: label, kelengkapan bukti, prior-art, matriks authz, skor dampak."""

import pytest

from wth_ai.kb.db import connect, upsert_cve
from wth_ai.redteam.novel.artifacts import REQUIRED, may_confirm, missing_artifacts
from wth_ai.redteam.novel.diffauth import Probe, analyze
from wth_ai.redteam.novel.impact import WEIGHTS, impact_score
from wth_ai.redteam.novel.labels import (CANDIDATE_NOVEL, CONFIRMED_NOVEL,
                                         Provenance, assert_novel_has_no_cve,
                                         label)
from wth_ai.redteam.novel.prior_art import resolve_provenance


def test_novel_cannot_cite_cve():
    with pytest.raises(ValueError):
        assert_novel_has_no_cve("ini mirip CVE-2021-44228")


def test_novel_label_is_confirmed_or_candidate():
    assert label(Provenance.NOVEL, confirmed=True) == CONFIRMED_NOVEL
    assert label(Provenance.NOVEL, confirmed=False) == CANDIDATE_NOVEL


def test_cve_and_cwe_labels():
    assert label(Provenance.CVE_BACKED, confirmed=True,
                 cve="CVE-2021-44228") == "KNOWN (CVE-2021-44228)"
    assert label(Provenance.CWE_PATTERN, confirmed=True,
                 cwe="CWE-639") == "KNOWN (CWE-639)"


def test_artifacts_required_list_has_seven_items():
    assert len(REQUIRED) == 7


def test_empty_bundle_cannot_confirm(tmp_path):
    assert len(missing_artifacts(tmp_path)) == 7
    assert may_confirm(tmp_path) is False


def test_missing_control_blocks_confirmation(tmp_path):
    for name in REQUIRED:
        if name == "control":
            continue
        (tmp_path / name).write_text("x")
    assert may_confirm(tmp_path) is False
    (tmp_path / "control").write_text("x")
    assert may_confirm(tmp_path) is True


def test_prior_art_hit_downgrades_to_cve_backed(tmp_path):
    conn = connect(tmp_path / "kb.sqlite")
    upsert_cve(conn, {"cve_id": "CVE-2019-0001", "summary": "bola order object",
                      "product": "orders", "assessment_ready": True})
    provenance, hits = resolve_provenance(conn, product="orders",
                                          text="bola order object")
    assert provenance is Provenance.CVE_BACKED
    assert hits and hits[0]["cve_id"] == "CVE-2019-0001"


def test_prior_art_miss_stays_novel(tmp_path):
    conn = connect(tmp_path / "kb.sqlite")
    provenance, hits = resolve_provenance(conn, product="unknownproduct",
                                          text="tidak ada yang mirip")
    assert provenance is Provenance.NOVEL
    assert hits == []


def test_diffauth_flags_cross_identity_200():
    result = analyze([
        Probe("A", "1001", "GET", 200),
        Probe("B", "1001", "GET", 200),
    ])
    assert len(result.candidates) == 1
    assert result.to_dict()["candidates"][0]["method"] == "GET"


def test_diffauth_ignores_correct_403():
    result = analyze([
        Probe("A", "1001", "GET", 200),
        Probe("B", "1001", "GET", 403),
    ])
    assert result.candidates == []


def test_impact_bands():
    assert impact_score(**{key: 1.0 for key in WEIGHTS}) == (100, "CRITICAL")
    score, band = impact_score(**{key: 0.8 for key in WEIGHTS})
    assert (score, band) == (80, "HIGH")


def test_impact_weights_sum_to_100():
    assert sum(WEIGHTS.values()) == 100