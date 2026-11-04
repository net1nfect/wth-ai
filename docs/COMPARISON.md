# WTH-AI vs Strix — hanya baris yang punya bukti

Kolom **bukti** berisi file/perintah yang bisa dijalankan ulang. Baris tanpa bukti
tidak boleh masuk tabel ini.

| Kapabilitas | Strix v1.7 | WTH-AI | Bukti |
|---|---|---|---|
| Mode agen (plan/build/auto/scout/strike/chain/scribe) | tidak ada (hanya scan-mode) | ada, `auto` = superset, mode lain pecahan | `pytest tests/test_modes.py -q` |
| Kedalaman scan quick/standard/deep | ada | ada (angka eksplisit, masuk `run.json`) | `pytest tests/test_scan_modes.py -q` |
| Headless + exit code 0/1/2 + `--fail-on` | ada | ada | `pytest tests/test_cli_scan.py -q` |
| SARIF 2.1.0 | ada | ada (hanya CONFIRMED) | `pytest tests/test_sarif.py -q` |
| Artefak per-temuan `.md` + `vulnerabilities.json/.csv` | ada | ada | `pytest tests/test_artifacts_write.py -q` |
| `run.json` status/usage/coverage | ada | ada | `pytest tests/test_run_meta.py -q` |
| Bundel bukti ber-hash + verifikasi | tidak ada | ada (`manifest.json`, `audit.jsonl` ber-rantai, `root_hash.txt`) | `pytest tests/test_bundle.py -q` · `wth verify out/bundle/` |
| Containment terbukti (network internal, tanpa port) | ada (Docker) | ada (argumen + bukti eksekusi) | `docs/evidence/f1-containment.txt` |
| Allowlist egress + audit `EGRESS_DENIED` | scope host | ada, dua lapis (`ScopeManager` + `netguard`) | `pytest tests/test_netguard.py -q` |
| Grading klaim berbasis bukti (request/response + kontrol negatif) | tidak ada | ada (`veracity.py`), status tak bisa dipalsukan | `pytest tests/test_redteam_classify.py -q` |
| Temuan tanpa CVE (provenance + prior-art + skor dampak) | tidak ada | ada | `pytest tests/test_novel.py -q` |
| KB CVE lokal + validator sitasi gagal keras | tidak ada | ada (SQLite FTS5) | `pytest tests/test_kb.py tests/test_cli_kb.py -q` |
| MCP digerbangi per mode + redaksi argumen | tidak ada gating mode | ada | `pytest tests/test_mcp_gating.py -q` |
| Skill (SKILL.md) | ada | ada (registry repo + `wth skill list`) | `pytest tests/test_skill_bola.py -q` |
| Auto-fix PR (patch + retest) | ada | **tidak ada, sengaja** — membalik invarian "tidak mengubah state target" | — |
| Web dashboard (`strix view`) | ada | **tidak ada, sengaja** — `AGENTS.md` melarang dashboard; diganti `wth report --format html` (file statis) | `pytest tests/test_report_html.py -q` |
| Proxy Caido / browser Playwright / runtime Kali / PDF | ada | belum (F8, hanya bila waktu tersisa) | — |
| OT/ICS (Modbus) | tidak ada | belum (F8.7, rencana `dry-run` read-only) | — |

Bacaan jujur: WTH-AI tidak mengejar paritas mesin. Yang diambil adalah paritas
**antarmuka** (mode, exit code, artefak, SARIF) supaya bisa dipakai di CI, lalu
dimenangkan di tempat Strix tidak masuk: bundel bukti yang bisa diverifikasi,
temuan tanpa CVE dengan prior-art, dan containment yang bisa dibuktikan dengan
satu perintah docker.