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
| Bundel bukti ber-hash + verifikasi | tidak ada | ada (`manifest.json`, `audit.jsonl` ber-rantai, `root_hash.txt`) | `pytest tests/test_bundle.py -q` · `wth-ai verify out/bundle/` |
| Containment terbukti (network internal, tanpa port) | ada (Docker) | ada (argumen + bukti eksekusi) | `docs/evidence/f1-containment.txt` |
| Allowlist egress + audit `EGRESS_DENIED` | scope host | ada, dua lapis (`ScopeManager` + `netguard`) | `pytest tests/test_netguard.py -q` |
| Grading klaim berbasis bukti (request/response + kontrol negatif) | tidak ada | ada (`veracity.py`), status tak bisa dipalsukan | `pytest tests/test_redteam_classify.py -q` |
| Temuan tanpa CVE (provenance + prior-art + skor dampak) | tidak ada | ada | `pytest tests/test_novel.py -q` |
| KB CVE lokal + validator sitasi gagal keras | tidak ada | ada (SQLite FTS5) | `pytest tests/test_kb.py tests/test_cli_kb.py -q` |
| MCP digerbangi per mode + redaksi argumen | tidak ada gating mode | ada | `pytest tests/test_mcp_gating.py -q` |
| MCP transport | stdio + http (`auth.kind=bearer`, `allowed_tools` per server) | stdio saja; http/bearer menyusul | `grep -c "class _HttpMCPConnection" wth_ai/mcp/manager.py` (0) · rencana: `docs/superpowers/plans/2026-11-07-port-strix.md` Task 8 |
| Agen graph (spawn/send/wait antar-agen) | ada | belum — `delegate_task` satu arah, kedalaman terbatas | `grep -n "MAX_DEPTH" wth_ai/agents/subagent/registry.py` (`MAX_DEPTH = 2`) · rencana Task 11 |
| SAST codebase (opengrep) | ada | belum — target hanya URL/spec | `grep -rl opengrep wth_ai` (kosong) · rencana Task 6 |
| Auth langganan (ChatGPT) | ada | tidak ada — provider daftar eksplisit di config | `grep -n "PROVIDER_NAMES" wth_ai/config.py` |
| Cloud + REST API | ada (`strix cloud`, billing) | tidak ada | `grep -rl "strix cloud\|cloud_api" wth_ai` (kosong) |
| Skill (SKILL.md) | ada | ada (registry repo + `wth-ai skill list`) | `pytest tests/test_skill_bola.py -q` |
| Auto-fix PR (patch + retest) | ada | **izin berbatas, belum ada** — hanya `full_lab` + `--destructive`, tanpa `git push` | `grep -n "izin berbatas" AGENTS.md` · rencana Task 15 |
| Web dashboard (`strix view`) | ada | **izin berbatas, belum ada** — `127.0.0.1`, read-only, bertoken; pengganti sekarang: `wth-ai report --format html` | `pytest tests/test_report_html.py -q` · rencana Task 14 |
| Browser Playwright (screenshot CLI) | ada | ada — `wth-ai browser capture`, tanpa import browser (subprocess) | `pytest tests/test_browser_capture.py -q` |
| Runtime Kali (sandbox container) | ada | ada — `wth-ai sandbox plan/run`, argv docker dibangun `containment.py` | `pytest tests/test_cli_ot_browser_sandbox.py tests/test_cli_sandbox_visual.py -q` |
| Laporan PDF | ada | ada — `wth-ai report --format pdf`, tanpa dependency | `pytest tests/test_report_pdf.py -q` |
| Recon visual (clustering screenshot) | tidak ada | ada — `wth-ai visual cluster` (dhash PNG) | `pytest tests/test_visual_cluster.py -q` |
| OT/ICS (Modbus read-only) | tidak ada | ada — `wth-ai ot plan/read`, hanya FC 1–4, tidak pernah menulis ke PLC | `pytest tests/test_ot_client.py tests/test_ot_modbus.py -q` |
| Proxy intercept (Caido/Burp/ZAP) | ada (produk UI) | ada — `WTH_PROXY` mengarahkan semua `recon.http.fetch`; EgressGuard tetap jalan lebih dulu | `pytest tests/test_recon_proxy.py -q` |

Bacaan jujur: WTH-AI tidak mengejar paritas mesin. Yang diambil adalah paritas
**antarmuka** (mode, exit code, artefak, SARIF) supaya bisa dipakai di CI, lalu
dimenangkan di tempat Strix tidak masuk: bundel bukti yang bisa diverifikasi,
temuan tanpa CVE dengan prior-art, dan containment yang bisa dibuktikan dengan
satu perintah docker.