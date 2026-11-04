"""Builder KB: record tanpa CVSS tidak siap-assessment, FTS bisa mencari."""

import json

from wth_ai.kb.build import build, load_epss, load_kev, normalize_nvd_record
from wth_ai.kb.db import connect, count

NVD_FULL = {
    "vulnerabilities": [{
        "cve": {
            "id": "CVE-2021-44228",
            "published": "2021-12-10T10:15:09.143",
            "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 10.0}}]},
            "weaknesses": [{"description": [{"value": "CWE-502"}]}],
            "configurations": [{"nodes": [{"cpeMatch": [{
                "criteria": "cpe:2.3:a:apache:log4j:2.14.1:*:*:*:*:*:*:*"}]}]}],
            "descriptions": [{"lang": "en", "value": "JNDI remote code execution"}],
        }
    }]
}

NVD_NO_CVSS = {
    "vulnerabilities": [{
        "cve": {
            "id": "CVE-2020-0001",
            "published": "2020-01-01T00:00:00.000",
            "metrics": {},
            "weaknesses": [{"description": [{"value": "CWE-79"}]}],
            "configurations": [],
            "descriptions": [{"lang": "en", "value": "Belum dianalisis"}],
        }
    }]
}


def test_record_without_cvss_is_not_assessment_ready():
    record = normalize_nvd_record(NVD_NO_CVSS["vulnerabilities"][0])
    assert record["assessment_ready"] is False
    assert record["cvss"] is None


def test_complete_record_is_assessment_ready():
    record = normalize_nvd_record(NVD_FULL["vulnerabilities"][0])
    assert record["assessment_ready"] is True
    assert record["product"] == "apache:log4j"
    assert record["cwe"] == "CWE-502"


def test_build_fills_db_and_fts(tmp_path):
    source = tmp_path / "src"
    (source / "nvd").mkdir(parents=True)
    (source / "nvd" / "part.json").write_text(json.dumps(NVD_FULL), encoding="utf-8")
    (source / "kev.json").write_text(json.dumps(
        {"vulnerabilities": [{"cveID": "CVE-2021-44228"}]}), encoding="utf-8")
    (source / "epss.csv").write_text("cve,epss\nCVE-2021-44228,0.97\n",
                                     encoding="utf-8")

    conn = connect(tmp_path / "kb.sqlite")
    summary = build(source, conn)
    assert summary["total"] == 1
    assert summary["ready"] == 1
    assert summary["kev"] == 1

    row = conn.execute("SELECT * FROM cve WHERE cve_id='CVE-2021-44228'").fetchone()
    assert row["kev"] == 1
    assert abs(row["epss"] - 0.97) < 1e-9

    hits = conn.execute("SELECT cve_id FROM cve_fts WHERE cve_fts MATCH 'log4j'").fetchall()
    assert [hit["cve_id"] for hit in hits] == ["CVE-2021-44228"]
    assert count(conn) == (1, 1)


def test_kev_only_entry_is_indexed(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "kev.json").write_text(json.dumps(
        {"vulnerabilities": [{"cveID": "CVE-2023-1111"}]}), encoding="utf-8")
    conn = connect(tmp_path / "kb.sqlite")
    summary = build(source, conn)
    assert summary["total"] == 1
    assert summary["ready"] == 0


def test_missing_source_directory_raises(tmp_path):
    conn = connect(tmp_path / "kb.sqlite")
    try:
        build(tmp_path / "tidak-ada", conn)
    except FileNotFoundError:
        return
    raise AssertionError("direktori hilang seharusnya melempar FileNotFoundError")


def test_helpers_parse_kev_and_epss(tmp_path):
    kev = tmp_path / "kev.json"
    kev.write_text(json.dumps({"vulnerabilities": [{"cveID": "cve-2024-1"}]}))
    assert load_kev(kev) == {"CVE-2024-1"}
    epss = tmp_path / "epss.csv"
    epss.write_text("cve,epss\nCVE-2024-1,0.5\n")
    assert load_epss(epss) == {"CVE-2024-1": 0.5}