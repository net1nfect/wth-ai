# Phase 0 — Sisa (item 4 & WU-209): audit prompt + CI hardening

Tanggal: 2026-11-04 · Branch `agentic` · Baseline: suite 1321 passed / 0 failed.

## Keputusan audit prompt (dibaca penuh, gap nyata)

| Prompt | Dipakai oleh | Status |
|---|---|---|
| `supervisor_system.txt` | `agents/supervisor.py:37` | OK — doktrin adaptif v2 sudah ada (test mengunci) |
| `planner_system_v5.txt` | `agents/planner.py:60` (menang bila ada) | OK |
| `planner_system.txt` | `planner.py:61` fallback | OK |
| `chat_system.txt` | `agents/chat.py:44` | OK |
| `verifier_system.txt` | `agents/verifier.py:20` | OK |
| `report_system.txt` | `agents/report_builder.py:24` | OK |
| `wth_system.txt` | **tidak ada loader** | ORPHAN — diarsipkan |

**Gap nyata: `executor_system.txt` masih doktrin v2.0** — "v2.0", "Agen 2",
larangan naif ("JANGAN reverse shell, malware, persistence, exfiltrasi data
nyata"), dan daftar contoh aman. Tidak menyebut tool adaptive (`compare_to`,
`[DIFF vs TX]` — hal yang nyata ada di `http_probe` sekarang), tidak menyebut
opt-in destruktif berbatas, tidak menyebut callback/OOB.

**Keputusan**: edit in-place `executor_system.txt` (bukan `executor_system_v3.txt`).
Alasan: dua test membaca file itu langsung (`tests/test_adaptive_testing.py:168`),
dan pola `v5 or fallback` di planner membuktikan file versi-ganda itu sumber
drift. Satu file = satu kebenaran; versi ditandai di dalam dokumen.

## Checklist

- [x] Audit 8 prompt + peta loader (semua terverifikasi)
- [x] `executor_system.txt` → doktrin v3 (adaptive + destruktif berbatas + callback)
- [x] Arsipkan orphan `wth_system.txt` → `docs/archive/`
- [x] Perkuat 2 test kontrak prompt
- [x] `.dockerignore` baru (WU-209)
- [x] `ci.yml`: job security scanning (bandit + pip-audit)
- [x] Fix HIGH bandit B324 (`usedforsecurity=False`) + B602 (`# nosec` + justifikasi)
- [x] Fix MEDIUM yang sah: B113 (timeout eksplisit), B108 (tempfile), B310 (`# nosec`)
- [x] `# nosec` dengan justifikasi: B608 (query parameterized), B104 (hint string, bukan bind)
- [x] Bump `python-dotenv` 1.0.0 → 1.2.2 (PYSEC-2026-2270)
- [x] Gate akhir: suite hijau + bandit/pip-audit bersih pada severity yang di-gate

## Temuan bandit (16 medium+) — putusan

| Kode | Lokasi | Putusan |
|---|---|---|
| B324 MD5 | `probe/diff_brain.py:71` | fix: `usedforsecurity=False` (fingerprint non-kripto) |
| B324 SHA1 | `recon/realtime.py:32` | fix: `usedforsecurity=False` (slug, bukan kripto) |
| B602 shell=True | `tools/installer.py:66` | `# nosec` — argv tak bisa dibangun untuk installer `go install`/`snap`/`script`; binary dari PATH operator |
| B108 temp | `computer_use/session.py:262` | fix: `tempfile.gettempdir()` (sinkron dgn `_runtime_dir()` modul yang sama) |
| B104 bind 0.0.0.0 | `hunt/proactive.py:42`, `recon/planner.py:44` | `# nosec` — literal di tuple hint SSRF/private, BUKAN bind |
| B608 SQL | `jobs/store.py:93,130,140,206,241` | `# nosec` — fragment adalah nama kolom konstan + placeholder `?`; nilai selalu parameterized |
| B113 timeout | `ollama.py:112`, `openai_compat.py:193,221` | fix: `req_timeout = timeout or self.default_timeout` lalu `timeout=req_timeout` (bandit tak bisa resolve expr `or`) |
| B310 urlopen | `skills/hub.py:118` (sudah `# noqa: S310`) | tambah `# nosec B310` |
| B310 urlopen | `tools/doctor.py:210,225` | `# nosec B310` — URL dari config operator, skema https |

Catatan kejujuran: B602 adalah deklarasi risiko yang diketahui, bukan perbaikan.
Menutupnya butuh installer argv-native untuk 10 package manager (pekerjaan
tersendiri); `# nosec` + justifikasi adalah pilihan yang jujur untuk sekarang.

## Dampak yang TIDAK diambil

- TIDAK memperbaiki B602 (butuh redesign installer) — dicatat sebagai follow-up.
- TIDAK mengaktifkan gate bandit `-lll` (high only) di CI: codebase ini punya
  banyak pola sadar (payload string, temp dir) yang akan jadi noise. Gate =
  `-ll` tapi SEMUA temuan diselesaikan, sehingga CI hijau bermakna.
- TIDAK mengubah `bootstrap.sh`/`install.sh` (WU-211 npm checksum) — di luar WU-209.

## Temuan tambahan saat verifikasi: suite TIDAK hermetis (ditemukan & diperbaiki)

Ditemukan saat menjalankan suite dengan **jaringan eksternal diblokir** (proxy
mati + `NO_PROXY=localhost`) sebagai simulasi CI:

1. **`tests/test_role_wiring.py::test_planner_calls_with_planner_role`** —
   `PlannerAgent.plan()` mencoba `chat_json_for_role` LEBIH DULU; spy test hanya
   mencegat `chat_for_role`. Di mesin dengan endpoint LLM hidup (`custom_base_url`),
   test ini membuat panggilan LLM NYATA: kalau model menjawab JSON valid, fallback
   tidak terjadi → spy kosong → FLAKY (teramati: satu run 1322 pass, run berikut
   3 failed, berikutnya hijau lagi tanpa perubahan kode terkait).
   **Fix**: helper `_fresh_orch_with_spy` memock `chat_json_for_role` (pola sama
   dengan `test_provider.py`) → jalur deterministik tanpa jaringan.
2. **`tests/test_wth_ai.py::test_vuln_idor_executor_probes_without_accounts`** —
   mengirim request NYATA ke `https://x.com` pada setiap run suite.
   **Fix**: stub `requests.get` (dua respons objek berbeda) + target diganti ke
   domain reserved `.test` + assertion diperkuat (2 call, risk `high`).

## Bukti verifikasi (angka nyata, bukan klaim)

- RUN normal 1, 2, 4: **1322 passed / 0 failed** (berulang).
- RUN 5 dengan jaringan eksternal diblokir + localhost dikecualikan:
  **1322 passed / 0 failed** — suite terbukti tidak membutuhkan jaringan eksternal.
- `bandit -r wth_ai -ll`: exit 0, **0 issues** (dari 16 medium+ sebelum).
- `pip-audit -r requirements.txt`: **No known vulnerabilities found**.

