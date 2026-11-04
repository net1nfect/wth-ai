"""Bangun KB dari file mentah yang sudah diunduh operator.

Module ini **tidak** mengunduh apa pun: unduhan adalah langkah terpisah, supaya
build bisa direproduksi offline dan tidak ada permintaan jaringan yang diam-diam
di dalam perintah build.

Bentuk masukan yang didukung:
  nvd/*.json   respons NVD 2.0 ({"vulnerabilities": [...]}) atau list record
  kev.json     katalog CISA KEV ({"vulnerabilities": [{"cveID": ...}]})
  epss.csv     kolom pertama cve, salah satu kolom bernama epss
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from wth_ai.kb.db import set_meta, upsert_cve

REQUIRED = ("cve_id", "published", "cvss", "kev", "epss", "assessment_ready",
            "cwe", "product", "summary")


def _first(value, default=None):
    """Ambil elemen pertama dari list hasil query API, atau nilai apa adanya."""
    if isinstance(value, list):
        return value[0] if value else default
    return default if value is None else value


def _cvss(cve: dict) -> float | None:
    metrics = cve.get("metrics") or {}
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV40", "cvssMetricV2"):
        entry = _first(metrics.get(key) or [])
        if entry:
            score = (entry.get("cvssData") or {}).get("baseScore", entry.get("baseScore"))
            if score is not None:
                return float(score)
    return None


def _cwe(cve: dict) -> str:
    for weakness in cve.get("weaknesses") or []:
        for description in weakness.get("description") or []:
            value = str(description.get("value", ""))
            if value.upper().startswith("CWE-"):
                return value.upper()
    return ""


def _product(cve: dict) -> str:
    for configuration in cve.get("configurations") or []:
        for node in configuration.get("nodes") or []:
            for match in node.get("cpeMatch") or []:
                criteria = str(match.get("criteria", ""))
                parts = criteria.split(":")
                if len(parts) > 4:
                    return f"{parts[3]}:{parts[4]}"
    return ""


def _summary(cve: dict) -> str:
    for description in cve.get("descriptions") or []:
        if description.get("lang") == "en":
            return str(description.get("value", ""))[:500]
    return ""


def normalize_nvd_record(raw: dict) -> dict:
    cve = raw.get("cve", raw)
    cvss = _cvss(cve)
    product = _product(cve)
    return {
        "cve_id": str(cve.get("id", "")).upper(),
        "published": str(cve.get("published", ""))[:10],
        "cvss": cvss,
        "kev": False,
        "epss": None,
        "assessment_ready": bool(cvss is not None and product),
        "cwe": _cwe(cve),
        "product": product,
        "summary": _summary(cve),
    }


def load_nvd(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("vulnerabilities") if isinstance(data, dict) else data
    return [normalize_nvd_record(row) for row in (rows or [])]


def load_kev(path: Path) -> set[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("vulnerabilities") if isinstance(data, dict) else data
    return {str((row.get("cveID") or row.get("cve_id") or "")).upper()
            for row in (rows or [])} - {""}


def load_epss(path: Path) -> dict[str, float]:
    scores: dict[str, float] = {}
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            cve = ""
            score = None
            for key, value in row.items():
                key_norm = (key or "").strip().lower()
                if key_norm in {"cve", "cve_id", "cveid"}:
                    cve = (value or "").strip().upper()
                elif key_norm in {"epss", "score"}:
                    try:
                        score = float(value)
                    except (TypeError, ValueError):
                        score = None
            if cve and score is not None:
                scores[cve] = score
    return scores


def build(source: Path, conn: sqlite3.Connection) -> dict:
    """Baca direktori sumber dan isi DB. Kembalikan ringkasan yang dicetak CLI."""
    source = Path(source)
    if not source.is_dir():
        raise FileNotFoundError(f"direktori sumber tidak ada: {source}")

    records: dict[str, dict] = {}
    for path in sorted(source.glob("nvd/*.json")):
        for record in load_nvd(path):
            if record["cve_id"]:
                records[record["cve_id"]] = record

    kev_path = source / "kev.json"
    if kev_path.is_file():
        for cve_id in load_kev(kev_path):
            records.setdefault(cve_id, {"cve_id": cve_id, "published": "",
                                        "cvss": None, "kev": False, "epss": None,
                                        "assessment_ready": False, "cwe": "",
                                        "product": "", "summary": ""})
            records[cve_id]["kev"] = True

    epss_path = source / "epss.csv"
    if epss_path.is_file():
        for cve_id, score in load_epss(epss_path).items():
            if cve_id in records:
                records[cve_id]["epss"] = score

    for record in records.values():
        upsert_cve(conn, record)

    set_meta(conn, "built_at", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ready = sum(1 for record in records.values() if record["assessment_ready"])
    return {"total": len(records), "ready": ready, "kev": sum(
        1 for record in records.values() if record["kev"])}