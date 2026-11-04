"""KB: skema, sitasi, skor."""

from wth_ai import kb
from wth_ai.kb.cite import UngroundedCitation, cited_cves, validate_citations
from wth_ai.kb.db import connect, count, get_meta, set_meta, upsert_cve
from wth_ai.kb.score import WEIGHTS, priority, weights_line


def _conn(tmp_path):
    return connect(tmp_path / "kb.sqlite")


def _record(cve_id="CVE-2021-44228", **kwargs):
    base = {"cve_id": cve_id, "published": "2021-12-10", "cvss": 10.0,
            "kev": True, "epss": 0.97, "assessment_ready": True,
            "cwe": "CWE-502", "product": "log4j",
            "summary": "Log4Shell JNDI remote code execution"}
    base.update(kwargs)
    return base


def test_schema_is_created(tmp_path):
    conn = _conn(tmp_path)
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    assert {"cve", "cve_fts", "meta"} <= tables


def test_meta_round_trip(tmp_path):
    conn = _conn(tmp_path)
    set_meta(conn, "built_at", "2026-11-04")
    assert get_meta(conn, "built_at") == "2026-11-04"
    assert get_meta(conn, "missing", "fallback") == "fallback"


def test_upsert_and_count(tmp_path):
    conn = _conn(tmp_path)
    upsert_cve(conn, _record())
    upsert_cve(conn, _record("CVE-2020-0001", assessment_ready=False))
    assert count(conn) == (2, 1)


def test_fts_search_finds_record(tmp_path):
    conn = _conn(tmp_path)
    upsert_cve(conn, _record())
    rows = conn.execute(
        "SELECT cve_id FROM cve_fts WHERE cve_fts MATCH 'log4j'").fetchall()
    assert [row["cve_id"] for row in rows] == ["CVE-2021-44228"]


def test_validate_citations_accepts_known(tmp_path):
    conn = _conn(tmp_path)
    upsert_cve(conn, _record())
    assert validate_citations("lihat CVE-2021-44228", conn) == ["CVE-2021-44228"]


def test_validate_citations_rejects_unknown(tmp_path):
    conn = _conn(tmp_path)
    upsert_cve(conn, _record())
    try:
        validate_citations("CVE-2026-99999 tidak ada", conn)
    except UngroundedCitation as exc:
        assert "CVE-2026-99999" in str(exc)
    else:  # pragma: no cover - kegagalan test yang jelas
        raise AssertionError("sitasi palsu diterima")


def test_no_citations_returns_empty(tmp_path):
    conn = _conn(tmp_path)
    assert validate_citations("tidak ada CVE di sini", conn) == []
    assert cited_cves("CVE-2021-44228 dan cve-2021-44228") == ["CVE-2021-44228"]


def test_priority_weights_sum_to_one():
    assert round(sum(WEIGHTS.values()), 6) == 1.0


def test_priority_max_and_min():
    assert priority(10.0, True, 1.0, 1.0) == 1.0
    assert priority(None, False, None, 0.0) == 0.0


def test_priority_is_monotonic_in_kev():
    assert priority(5.0, True, 0.1, 0.5) > priority(5.0, False, 0.1, 0.5)


def test_weights_line_mentions_every_weight():
    line = weights_line()
    for name in WEIGHTS:
        assert name in line


def test_kb_package_exports():
    assert kb.UngroundedCitation is UngroundedCitation