# 02 — Rencana Superioritas vs Strix (Blueprint V2)

> Status: dokumen perencanaan. Disusun setelah pembacaan menyeluruh source code `wth_ai/` (280 file Python, ~39.500 baris), 103 file test (1.281 test function), seluruh `docs/` (PRD 001–013, ADR 001–013, planning 00/01, WORK-UNITS, master architecture), `wth-main.py`, prompt, dan riset eksternal tentang Strix (docs resmi + review pihak ketiga).
> Aturan dokumen ini: yang "sudah ada" merujuk file nyata; yang "belum" ditandai eksplisit; tidak ada klaim tanpa bukti. Kultur repo dipertahankan: no hollow architecture, no overclaim.

## 0. Fakta baseline (terverifikasi saat penulisan)

- Struktur: `wth_ai/` 280 file Python (~39.485 baris) + `wth-main.py` (launcher aktual) + 103 file test (1.281 `def test_`).
- Test suite dijalankan penuh: **1.317 passed, 2 failed, 72 detik**. Dua failure BUKAN kegagalan logika pipeline:
  - `tests/test_9router_skills.py:37` — mengharapkan 8, menemukan 15.
  - `tests/test_proactive_and_self_improvement.py:315` — mengharapkan 2, menemukan 9.
  - Akar masalah (dikonfirmasi di `wth_ai/skills/registry.py:40-49`): `SkillRegistry.discover()` selalu memindai 3 sumber — `openclaw_path`, `local_path`, DAN `wth_home()/skills` — sehingga test tidak hermetis begitu `~/.wth-ai/skills` berisi skill nyata (saat ini 7 skill auto). Ini bug isolasi test, bukan bug keamanan. Perbaikan: parameter `sources=` eksplisit atau monkeypatch `wth_home` di test.
- Git: branch `agentic`, HEAD `f4ce8d0` (2026-11-03). Working tree punya **53 file tracked termodifikasi (+3.402/−234) yang belum di-commit** (termasuk pekerjaan remediasi P0/P1: `scr*, policy`, `bots/auth`, `cli/entry`, dst) + dokumen audit untracked. Risiko kehilangan kerja nyata — commit dulu sebelum fase berikutnya.
- Data operasional (engagements/logs/results) tersimpan di dalam repo — perlu `.gitignore`/relokasi.

## 1. Posisi WTH-AI hari ini

### 1.1 Yang sudah kuat (dan jarang dimiliki pesaing)

| Area | Bukti di kode |
|---|---|
| Eksekusi terkontrol | `terminal/bridge.py` + `permission_manager.py` + `scope_manager.py` + `policy/engine.py` (3/6 layer nyata: global denylist → tool policy → action policy; fail-closed) + `approval` http_probe + `redaction.py` kanonik + SSRF guard `recon/http.py` + `vault.py` (Fernet) |
| Anti-halusinasi | `veracity.py` (confirmed butuh request/response + reproduksi + kontrol negatif), TTST `[TX:...]`, `GroundingFirewall`, `claim_store.py` + transition guard `FindingState`, `audit_log.py` (SQLite sink, ter-wire ke daemon) |
| Otak serang | recon engine (manifest 215 tool + katalog "200+ ToolSpec" + builtin Python), `graph/` + `query_graph`, `brain/hypothesis_engine` (rule-based), `brain/chain_planner` (template), `agents/planner` (LLM JSON), `executor/vuln/*` (IDOR/SSRF/XSS/auth/race dengan pembuktian nyata: IDOR tanpa akun, SSRF dibuktikan isi, login probe, race method benar) |
| Adaptif & proaktif | uji adaptif (sinyal respons, diff antar-request, `compare_to`), `hunt/proactive.py` (langkah berikutnya dari temuan), `hunt/autopilot.py` (pass adaptif + cron `every 6h`), `hunt/payloads.py` (59 payload / 12 kelas, blast-radius berbatas + pemulihan), `hunt/generator.py` (AI generate payload dengan constraint), `research/engine.py` (fetch ber-guard SSRF + browser opsional), `intel/feed.py` |
| Sarana operator | browser `nodriver` (Cloudflare detect/bypass, form, login_flow), `computer_use` (Wayland/X11, verifikasi efek), cron, jobs/worker, gateway daemon, bot Discord/Telegram, MCP client+server, skills (hub/fusion/autowrite/guard), memory (FTS + learning + weights), token accounting + budget, CLI+REPL+TUI, ~50 slash command, 7 provider |
| Kultur | docs audit jujur, PRD/ADR/WU dengan exit review adversarial, "reachability honesty", no hollow architecture |

### 1.2 Yang belum selesai (semua sudah tercatat di WORK-UNITS — bukan temuan baru)

- `ExecutionBroker` masih skeleton: 6/9 tahap belum ada (fetch-fresh Engagement, taint, approval action-hash, sandbox+RunningActionRegistry, audit sinkron penuh, evidence-append). WU remainder 101/102/104/105/106/111/114/117 konvergen ke satu prasyarat ini.
- Kill-switch baru interrupt-check in-process; `RunningActionRegistry` (kill process-group) belum ada.
- `context.tainted` (anti prompt-injection struktural) belum ada.
- Approval belum satu otoritas; WU-115 (hapus 7 call-site evidence lama) BLOCKED menunggu broker auto-append.
- `prompts/executor_system.txt` masih doktrin lama v2.0 ("JANGAN reverse shell…") — perlu audit + versi baru selaras kapabilitas sekarang.
- 2 test non-hermetis (di atas); data operasional bercampur di repo.

## 2. Strix: apa itu, dan di mana dia rapuh (dari review pihak ketiga)

**Parity Strix** (docs resmi): toolkit lengkap (Caido proxy, Playwright browser, terminal, Python runtime, nuclei/ffuf), multi-agent graph, validasi PoC, autofix patch, CI/CD, cloud SaaS. Berjalan di Docker sandbox.

**Kelemahan terdokumentasi publik** (bukan opini kita — ini celah yang reviewer independen temukan):

1. **Biaya**: ~$17 untuk sekali quick scan (review protego.me); benchmark independen $20–$55 per run; sempat membuat API key otomatis dinonaktifkan karena spike token. "Tidak ada sweet spot murah" — butuh model mahal.
2. **Konsistensi**: run berbeda hasil berbeda; model kecil = false positive acak; review profesional menyimpulkan "deliver neither reliably nor consistently… unusable in practice" (security.sauer.ninja).
3. **Discovery rute buta**: 1.300 dari ~1.350 request blackbox adalah 404 karena menebak path datar; hanya ~30 menyentuh endpoint nyata (protego.me) — boros budget karena tidak punya jalan masuk kontrak API.
4. **Output & UX**: laporan hanya Markdown tanpa struktur tetap (tidak bisa jadi deliverable klien); crash saat output model tak terduga; flow background kadang macet; agen sering mengerjakan ulang tugas yang sama (sauer.ninja).
5. **Callback terputus**: engine Docker membuat reverse shell / SSRF callback in-bound sering gagal out-of-the-box (theaq.blog) — mereka menyiasati lewat instruksi prompt, bukan infrastruktur.
6. **Reputasi "vibe-coded"**: HN mempertanyakan burden of proof; screenshot temuan yang ditonjolkan justru lemah (celah JWT yang bisa ketahuan code review biasa).

**Implikasi strategi**: JANGAN bertarung di lane Strix Cloud (SaaS, autofix PR, CI pemindaian generik). Lawan di lane yang reviewer sendiri bilang paling penting tapi paling lemah hari ini: **konsistensi, disiplin scope, biaya, bukti/OOB, kedalaman eksploitasi, dan laporan siap kirim.**

## 3. Tujuh sumbu diferensiasi (keputusan produk)

1. **Deterministik-dulu, LLM-terakhir** — rules + graph + hypothesis engine + katalog payload jalan sebelum model; model mengisi slot kreatif yang terbatas. (Sudah jadi DNA repo; perkuat.)
2. **Evidence-or-label** — tidak ada klaim tanpa bukti; tidak ada bukti → label "kandidat". Kontrol negatif wajib untuk "confirmed". (Veracity sudah ada; perluas ke seluruh output + report.)
3. **Disiplin engagement sebagai fitur** — authorization record, fetch-fresh status, audit chain, kill-switch, taint. Ini yang reviewer industri sebut dealbreaker jika tidak ada ("agen di luar scope = tidak deployable").
4. **Destruksi berbatas + rollback** — satu-satunya di pasar: uji destruktif yang di-opt-in, ber-blast-radius, dengan langkah pemulihan tertulis + audit. Strix default non-destruktif; kita menawarkan keduanya, terkendali.
5. **Cost governor** — budget per engagement/fase, policy cheap-first, "cost per confirmed finding" sebagai metrik pertama, bukan setelahnya.
6. **Terukur, bukan diklaim** — benchmark internal (find-rate, FP-rate, stabilitas pass@k, biaya, waktu); regresi memblok rilis. Ini jawaban langsung ke kelemahan #2 Strix.
7. **Lokal-first + MCP interop** — jalan penuh di mesin operator tanpa cloud; MCP memungkinkan menyambung toolkit lain (termasuk Burp/mitmproxy pihak ketiga).

## 4. Fitur yang perlu ada (F1–F20)

Format: ID — masalah → desain inti → depends → acceptance (bukti DONE).

### Grup A — Execution plane v2 (fondasi "serangan nyata")

- **F1 — Selesaikan ExecutionBroker jadi chokepoint tunggal (P0).** Masalah: 5 jalur eksekusi masih tersebar; kill-switch taint belum ada. Desain: tuntaskan sisa WU-101..117 (fetch-fresh `Engagement` di setiap execute, `RunningActionRegistry` + `start_new_session=True` (kill process-group), satu `ApprovalRegistry` action-hash single-use, `context.tainted`, audit sinkron risk≥MEDIUM, broker auto-append evidence → hapus 7 call-site lama WU-115). Depends: tidak ada (fondasi). Acceptance: test matrix (permission × scope-auth × approval × tainted) semuanya fail-closed kecuali kombinasi diizinkan; kill-switch mematikan process-tree nyata; 14 security invariant punya test.
- **F2 — Callback Center / OOB interaction server.** Masalah: blind SSRF/XXE/RCE/blind XSS tidak bisa dibuktikan tanpa callback; Strix pun kalah di sini (Docker callback). Desain: listener HTTP+DNS lokal milik operator, token per-engagement + per-claim, auto-expire, semua hit tercatat ke evidence `[TX:...]`; scope-gated; tidak wajib domain publik (default localhost/tunnel opsional). Depends: F1 (scope + audit). Acceptance: lab blind-SSRF → hit tercatat, evidence + report otomatis; token mati setelah expire. **Status: MVP SELESAI 2026-11-04** — HTTP listener loopback + token TTL & persist di `$WTH_HOME/oob/` + hit → transcript `[TX:...]` + tool `oob_create`/`oob_check` + slash `/oob`; 11 test baru, suite 1333 passed. Sisa: listener DNS, auto-wire `_try_mcp_oob` (ssrf executor), scope/taint hook (menunggu F1), wiring report (F16). Detail: `docs/planning/04-f2-callback-center.md`.
- **F3 — Proxy interception bridge (mitmproxy embedded).** Masalah: capture/replay/diff trafik manual belum ada; Strix pakai Caido (closed-ish). Desain: mitmproxy in-process, hanya host in-scope yang direkam, flow → evidence store (redaksi secret), tool agent: `proxy_flows` / `proxy_replay` / `proxy_diff` / `proxy_fuzz`; exporter HAR. Acceptance: siklus capture→replay→diff di lab; HAR import (lihat F7); nol rekaman host out-of-scope.
- **F4 — Browser sebagai tool agent (bukan hanya slash command).** Masalah: `BrowserController` (nodriver, Cloudflare bypass, forms, login_flow) sudah ada tapi TIDAK terdaftar di `tool_registry` — agent tidak bisa memanggilnya via tool-call. Desain: expose `browser_navigate`/`browser_snapshot`/`browser_click`/`browser_type`/`browser_screenshot`(→evidence)/`browser_cookies`(→F6); kombinasikan dengan F3. Acceptance: login flow + DOM XSS lab diselesaikan agent hanya via tool-call; screenshot masuk evidence; permission-gated (`browser_allowed` sudah ada).
- **F5 — Exploit runtime + tier sandbox.** Masalah: "exploit nyata / tulis kode nyata" butuh tempat menjalankan PoC dengan aman & terlacak. Desain: runtime Python per-engagement (subprocess: `rlimit`, env allowlist, timeout, kill group, output→artefak+audit); artefak versioned per claim; **lab mode** opsional container/microVM (trigger ADR-013 kini tercapai: eksekusi kode LLM-generated) — default tetap OS-level. Acceptance: PoC lab jalan, artefak + audit tersimpan, bisa dimatikan dari kill-switch, tidak ada bocoran env/host.
- **F6 — Session & identity manager.** Masalah: multi-akun/BOLA butuh cookie jar & re-auth yang aman; sekarang serba manual. Desain: session store (cookie jar, refresh token lifecycle, role per akun), semua kredensial via `vault:<name>`, auto re-auth, injeksi hanya di titik request broker. Acceptance: uji BOLA 2-akun end-to-end tanpa plaintext di log/transcript.
- **F7 — Target & spec intake.** Masalah: discovery buta = boros (bukti Strix). Desain: ingest OpenAPI/Swagger/Postman/HAR → seed AssetGraph ("known routes first"), GraphQL introspection import; recon memprioritaskan rute nyata sebelum fuzzing. Acceptance: spec → graph; jumlah request 404 turun drastis di lab; route dari spec mendapat skor prioritas.

### Grup B — Intelligence (naik kelas dari "scanner pintar")

- **F8 — Chain planner v2 (graf rantai).** Masalah: `chain_planner` sekarang template linear per kelas. Desain: graf precondition/effect antar temuan; kandidat chain rank by impact × confidence × cost; **verifikasi tiap hop** sebelum lanjut; chain jadi objek kelas satu di claim & report; pembelajaran per tech-stack. Acceptance: lab multi-hop (contoh: IDOR → eskalasi role) tereksekusi dengan bukti per step; tidak ada step tanpa bukti; chain gagal dilaporkan sebagai negatif.
- **F9 — Intel & hypothesis coupling v2 (version-aware).** Masalah: review Strix menemukan pola "CVE lama tak relevan dihabiskan sampai mati". Desain: fingerprint versi → kandidat CVE (feed cache) → verifikasi live dulu → baru klaim; feed `intel/feed.py` + `research` masuk sebagai kandidat berlabel; CVE tidak pernah jadi klaim tanpa bukti live. Acceptance: fingerprint → daftar kandidat ber-marker versi; kandidat tanpa verifikasi otomatis berstatus kandidat.
- **F10 — Verifier tournament.** Masalah: false positive & flaky run merusak kepercayaan (celah #2 Strix). Desain: verifier instance kedua (model berbeda) + klasifikasi reprodusibilitas (stabil / flaky / sekali) + minimalisasi PoC + kontrol negatif wajib; metrik FP per kelas. Acceptance: FP-rate turun terukur di benchmark; klaim flaky diberi label, bukan dipromosikan.
- **F11 — Parallel workers + blackboard.** Masalah: `SubagentRunner` sekuensial (max depth 2), 9 spec sudah ada tapi belum paralel. Desain: worker pool (paralel berbatas), shared blackboard (AssetGraph + ClaimStore + artefak), budget per worker, rate-limit per host dari engagement. Acceptance: surface lab besar terpetakan N× lebih cepat; bukti tidak tercampur antar worker; budget dihormati.

### Grup C — Measurement & learning (jawaban langsung ke kelemahan Strix)

- **F12 — Benchmark harness v2.** Target: Juice Shop, DVWA, VAmPI, crAPI, subset OWASP Benchmark, subset HTB retired, + target regresi dari engagement nyata operator. Metrik: confirmed findings, FP-rate, biaya ($), wall-time, stabilitas pass@3, cost per finding, time-to-first-finding. Acceptance: laporan berkala; regresi memblok rilis; angka dipakai untuk klaim (bukan marketing).
- **F13 — Cost governor.** Budget per engagement & per fase; policy escalation cheap→mahal; cache recon/intel; ringkas konteks; tampilkan biaya live + breakdown di report. Acceptance: hard-stop di budget; "cost per finding" muncul di report & eval.
- **F14 — Learning loop v2.** Bobot per TEKNIK (bukan hanya kelas); review queue untuk skill auto-write; retrieval lintas-engagement ("yang bekerja di stack ini"); decay sinyal lama. Acceptance: bobot berubah hanya setelah sampel minimum; perubahan terlihat di perilaku `proactive`/autopilot.

### Grup D — Ops & deliverable

- **F15 — Campaign manager.** Antrian multi-target/multi-program, file scope per program, resumable (job type "engagement"), isolasi evidence antar engagement. Acceptance: 3 program berjalan overnight, isolasi scope terbukti, laporan pagi.
- **F16 — Report system v2.** Pipeline Claim→finding; template H1/Bugcrowd/Immunefi; CVSS; skrip replay; bundel bukti (zip: transcript, request/response, screenshot, flow); checklist retest. Acceptance: temuan lab → report submission-ready tanpa edit manual; tiap klaim tertaut bukti.
- **F17 — Retest & watch.** Wiring `FIXED`/`RETEST_PASSED`; watch → change detection → retest queue bertarget; notifikasi bot; cron. Acceptance: perbaikan di lab → retest hijau; regresi → reopen otomatis.
- **F18 — Headless/CI mode (parity, opsional).** `wth run --headless --budget --out json/sarif` + exit code; untuk regresi lab di CI sendiri (bukan menyasar PR orang).

### Grup E — Guardrail yang jadi keunggulan

- **F19 — Taint & anti prompt-injection penuh.** `context.tainted` default-taint semua output tool, reset hanya di pesan manusia; konten tainted memaksa approval. Acceptance: test injeksi tidak bisa menaikkan izin.
- **F20 — Kill switch penuh.** Tiga permukaan (thread job, process-group subprocess, approval-pending), flag persisten, tidak pernah menghapus evidence/audit. Acceptance: kill saat job jalan → proses mati, evidence tetap utuh, audit mencatat.

## 5. Alur kerja target (workflow nyata end-to-end)

```
Intake        /engage start bounty|lab  → scope file + import spec/HAR (F7) + RoE record
              Engagement ACTIVE (approval manusia) → taint ON, budget set (F13)

Recon         deterministik: registry tools + builtin → graph (route dari spec dulu)
              passive intel (F9) → prioritization scoring

Plan          hypothesis engine (rule) → planner LLM → chain candidates (F8)
              Claim dibuat saat PLANNING → mengalir ke ActionIntent (F1)

Execute       broker (scope/perm/approval/audit/kill) ⇄ proxy (F3) + browser (F4)
              + Callback Center (F2) + exploit runtime (F5) + session (F6)
              autopilot menjalankan bounded steps; adaptif dari sinyal respons

Verify        verifier tournament (F10): reprodusibilitas + kontrol negatif
              status naik HANYA dengan bukti; flaky diberi label

Deliver       report bundle (F16) + retest queue (F17)
Learn         eval update weights (F14) + skill autowrite + benchmark (F12)
```

Mode operasi: sesi interaktif (CLI/TUI), Bot (remote), Autopilot/Cron (berkala berbatas), Campaign (overnight), Watch (realtime perubahan).

## 6. Roadmap fase

- **Phase 0 — Bersihkan meja (1 minggu). — SELESAI 2026-11-04.** Backlog P0/P1 di-commit (4 commit tematik + `3065025`); `.gitignore` + untrack 143 file runtime; skill registry hermetis; audit prompt + `executor_system` doktrin v3; `.dockerignore` + CI `security` job (bandit `-ll` + pip-audit) dengan 16 temuan medium+ dituntaskan (fix/nosec terdokumentasi); 2 test yang membuat panggilan jaringan nyata dihermetiskan. Suite **1322 passed / 0 failed** — normal DAN dengan jaringan eksternal diblokir. Detail: `docs/planning/03-phase0-finish.md`.
- **Phase 1 — Eksekusi nyata (2–3 minggu).** F1 → F2, F3, F4, F6, F7 (+ F19 sebagian). Gate demo: satu run lab end-to-end: browser login (F4) → capture proxy (F3) → blind SSRF terbukti via callback (F2). **Status berjalan: F2 MVP ✓ 2026-11-04** (`docs/planning/04-f2-callback-center.md`); F1 (fondasi broker) masih terbuka — hook scope/taint menyusul.
- **Phase 2 — Mesin serangan (3–4 minggu).** F5, F8, F9, F10. Gate: chain multi-hop tereksekusi ber-bukti; FP-rate turun terukur di benchmark mini; exploit runtime sandboxed + killable.
- **Phase 3 — Skala & ukur (3–4 minggu).** F11, F12, F13, F14. Gate: benchmark v1 publik-internal; budget hard-stop; worker paralel tanpa cross-talk.
- **Phase 4 — Operasi & deliverable (2–3 minggu).** F15, F16, F17, F18. Gate: campaign overnight; report submission-ready; retest loop jalan.

Definisi DONE per fitur (kultur repo): test + bukti run nyata di lab + doc diperbarui. Fitur tanpa konsumen nyata TIDAK dibangun (no hollow architecture).

## 7. Metrik anti-overclaim

| Metrik | Cara ukur | Target awal |
|---|---|---|
| Confirmed findings | benchmark F12 | naik vs baseline |
| FP-rate | benchmark + verifier tournament | turun, terukur |
| Stabilitas pass@3 | 3 run target sama | ≥2/3 konsisten (Strix kelemahan #2) |
| Cost per finding | token accounting + F13 | turun; tampil live |
| Time-to-first-finding | benchmark | turun |
| Scope violations | audit + test invariant | **0 (hard invariant)** |
| Evidence coverage | % klaim ber-TX/EV | 100% untuk status "confirmed" |
| 404-budget ratio | statistik recon | turun setelah F7 |

Klaim "lebih canggih dari Strix" hanya boleh keluar SETELAH angka ini ada (dan opsional: jalankan Strix di lab yang sama untuk perbandingan head-to-head — reviewer sudah menunjukkan Strix lemah di konsistensi & biaya).

## 8. Keputusan lama yang perlu ditinjau ulang

- **ADR-013 (sandbox)**: trigger "eksekusi kode LLM-generated tanpa review" kini benar-benar terjadi (exploit runtime F5). Usul: tier-2 container/microVM untuk lab mode — bukan default, bukan pengganti OS-level yang sudah benar.
- **ADR-002 (task DAG)**: tetap ditolak untuk scheduler; F8 chain graph adalah objek pengetahuan/replanning, bukan DAG task — perjelas batas ini di ADR.
- **ADR-006 (routing otomatis)**: tetap; F13 menambah policy biaya eksplisit per fase (bukan routing konten otomatis) — beda hal, catat di ADR.
- **Non-goal dashboard**: tetap; opsi "mission control" TUI untuk multi-worker (P3+, opsional).

## 9. Risiko & mitigasi

- Scope creep → gate per fase + definisi DONE.
- Biaya LLM → F13 sebelum eksekusi berat; cache; cheap-first.
- Eksekusi destruktif → opt-in `destructive_allowed`, blast-radius, pemulihan tertulis, audit; mass TIDAK PERNAH auto.
- Legal → hanya authorized; scope gate fail-closed; record RoE.
- Kehilangan kerja uncommitted → Phase 0 commit.
- Ketergantungan tool eksternal → registry skip-gracefully (sudah ada); builtin fallback tetap.

## 10. Checklist minggu ini

1. [x] Commit backlog P0/P1 — 4 commit tematik (`2c35d7f`, `01a8574`, `b405c11`, `cf2c740`) + push origin agentic.
2. [x] `.gitignore` + untrack 143 file runtime (file tetap di disk).
3. [x] Isolasi `SkillRegistry` — parameter `sources=` eksplisit + `tests/test_skill_registry_sources.py`; suite **1321 passed / 0 failed** (`fix(skills)`).
4. [x] Audit `prompts/*.txt` (8 file + peta loader) — `executor_system.txt` kini doktrin v3 (uji adaptif + destruktif berbatas); prompt orphan `wth_system.txt` diarsipkan ke `docs/archive/`; callback sengaja DITUNDA sampai F2 (tidak diklaim sebelum ada). Detail: `docs/planning/03-phase0-finish.md`.
5. [ ] Tentukan daftar target benchmark v1 (F12) dan simpan sebagai config.
6. [x] Mulai implementasi F2 (Callback Center) — nilai diferensiasi tertinggi, dependensi paling kecil, dan langsung menutup kelemahan callbacks yang dimiliki Strix. **MVP SELESAI 2026-11-04** — HTTP loopback + token TTL + hit → `[TX:...]` + tool `oob_create`/`oob_check` + `/oob`; 11 test, suite **1333 passed**; sisa lanjutan di `docs/planning/04-f2-callback-center.md`.
