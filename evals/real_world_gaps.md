# EVALUASI JUJUR: Kesiapan WTH-AI sebagai Real-World Hacker

> Dokumen hidup. Tujuan: melampaui Strix. Setiap gap punya ID, bukti dari kangguan
> nyata (bukan asumsi), severity, dan arah perbaikan. **Dilarang overclaim di sini**:
> kalau belum diverifikasi live, status = "hipotesis", bukan "fakta".
>
> Log:
> - R1 (7 Nov 2026): baseline audit dari 8 kategori.
> - R2 (7 Nov 2026): verifikasi mendalam http_probe / run_shell / OOB / scanner.

---

## A. FAKTA YANG SEBELUMNYA TERKONFIRMASI (bukan gap — fondasi yang oke)

- OOB callback **nyata**: `wth_ai/oob/{dns,server,service,store}.py` — DNS server
  stdlib sendiri, tools `oob_create/oob_dns/oob_check` sudah teregistrasi untuk
  actor `planner`. Bisa melacak blind ping-back, hit tercatat sebagai evidence.
- Adaptive engine nyata & teruji live (403→200 `header_xff` bypass), 28 mutasi,
  vendor fingerprint Cloudflare/Akamai/?100 dsb.
- Malware forge: msfvenom real, XOR dropper, codec PYTHON/PS1/SH.
- `verify_offline` + `ReportBuilderAgent`: report builder ada.
- UX: TUI Textual (Ops tab, ctrl+6, NO-LIMITS badge), bifr/unrestricted otomatis drops
  semua gate tanpa human approval, self_improve + agent memory.

---

## B. GAP KLASIFIKASI KRITIS (blokir kesiapan dunia nyata)

### GAP-001 — Binary powerhouse terpasang tapi tidak tersistem
**Bukti:** `which` menunjukkan `nuclei`, `sqlmap`, `hydra`, `wfuzz` ADA di box; grep di
`wth_ai/` tidak menunjukkan adapter/dispatch untuk mereka (hanya `generate_manifest`,
pipeline builder). Agent hanya bisa `run_shell` mereka manual — output parser
tidak dietik ke findings graph, tidak masuk `evidence_pair`.
**Severity:** KRITIS. Alternatif: proxy adapter `stdout -> JSON/regex parser -> finding` per
scanner (nuclei -json, sqlmap -batch --output-dir).
**Fix:** tambahkan `ToolSpec execution_mode="external"` di registry + parser output.

### GAP-002 — `http_probe` tanpa dukungan sesi & cookie jar
**Bukti:** `wth_ai/terminal/bridge.py:http_probe()` — dipanggil `requests.request()`
TANPA `cookies=`, TANPA session jar antar request. `Authorization: vault:<name>` satu
cara kerja. Tidak ada login-flow automation (form creds / selenium).
**Dampak:** tidak bisa uji auth IDOR multi-account, tidak bisa reproduski flow
browser jwt/session. Strix punya browser automation built-in.
**Fix:** tambah cookie jar via `requests.Session` di bridge + `login` tool
(gunakan `test_accounts` di scope and/or vault creds; POST-credential, save cookie
ke session bridge, aktif on every subsequent probe).

### GAP-003 — `http_probe` tanpa dukungan proxy / intercept (mitm/Zap/Caido)
**Bukti:** grep `proxies=` di bridge — NOL hasil. Tidak ada mode intercept.
**Severity:** TINGGI untuk burp-style pentest. Hacker nyata butuh tunnel probe
through proxy untuk perekam request/response yang bisa di-edit ulang (replay).
**Fix:** tambah `--proxy arg` di bridge `http_probe` + auto kerekaman semua request
menjadi reproduskiable `tx` di transcript yang bisa replay.dbo.

### GAP-004 — redirect di-adopt `requests` tanpa rekap figur root-cause
**Bukti:** `allow_redirects=True` hardcoded di `http_probe()`, response body
terakhir doang; hop intermediate hilang dari evidence.
**Dampak:** open redirect bug teriekipasi silently; SSRF chain via redirect tidak
terekam pritas.
**Fix:** `allow_redirects=False` + manual tipelan loop yang men-echo setiap hop,
dengan `tx` chain yyg rekap diproses `verify`.

### GAP-5 — multipart/file upload tidak bisa dipenuhi
**Bukti:** `http_probe()` param cuma `body` (str) — `files=` tidak ada. Grep `multipart`
di kode: nol hasil.
**Dampak:** tidak bisa uji upload filter evasion / webshell/RCE via upload.
**Fix:** tambah `files=` di adapter + payload preset (php/jsp/asp/aspx/shell
 + bypass WAF double-extension, polyglot, null byte).

### GAP-6 — WebSocket probing tidak ada
**Bukti:** grep `websocket` di `wth_ai/` — nol hasil.
**Dampak:** kelas CSWSH (Cross-Site WebSocket Hijacking), token auth WS, WS-origin
check bypass — semua tidak bisa diserang.
**Fix:** tool `ws_probe` (wrong-authorize origin, tokenless connect) → butuh
library `websockets` ( websocket-client). Gwa test setelah: tambah registry.

### GAP-7 — Docker sandbox belum ada (run_shell langsung di host operator)
**Bukti:** `run_shell_live` disekusi via python subprocess langsung di host.
`self_improve` bisa menulis script code sendiri yang bisa di-run sendiri — potential
rl loop escapement (agent menulis kembali code di host operator).
**Dampak:** risiko agent meng-escape di box sendiri. Juga tidak aman untuk
toolchain exploaratory yang jauh lebih bannyak (dirt malicious online payload).
**Fix:** optional flag `--sandbox docker|host` di Bridge; default host (kompatibilitas),
opsional docker support untuk wth-agent/workflow (Strix punya sandbox by default).

---

## C. GAP KECERDASAN KEDUA (mid-heavy: lanjutan dari Pivot 8 roadmap)

### GAP-008 — Rule-based propose_next_tests terlalu statis
**Bukti:** test: 2 request pair → hanya **1 usulan**. Sinyal yang tidak cocok aturan =
0 proposals.
**Fix:** hybrid: LLM dipanggil untuk snperspective propose, dengan hard-limit budget,
semangat pattern reuse, output harus berupa JSON `NextTest` dan diverifikasi schema
sebelum dijalankan (rate-limited per turn).

### GAP-009 — Tidak ada executor untuk kelas vuln besar
**Bukti:** `wth_ai/executor/vuln/` hanya: `idor`, `xss`, `race`, `ssrf`, `auth_jwt`.
**HILANG:** SQLi (union/time-blind), SSTI, CMDi, deserialization (Java pickle yso),
prototype pollution (Node), CRLF, path traversal, file upload, GraphQL deep,
JWT none/expiry kid, OAuth/OIDC flow abuse, kerberoast/AD.
**Dampak:** autopilot/adaptive bisa probe tapi jarang mencapai "temuan PoC-able"
dari kekeraban bug CTF wajar; tomar wonten hashtag temuan bandel."().
**Fix:** tambah `payloads` lanjut per gap + executor masing-masing, diuji di
vulnapp docker (JuiceShop / vulhub).

### GAP-010 — verify_offline terlalu naif & tidak memandu KB-dependent
**Bukti:** `verify_offline` — verdict cuma `len(finding.evidence) >= 2`, reasoning
"butuh LLM". LLM verifier agonide sekali dan tidak punya payload-diff nyata.
**Fix:** deterministik verify: compare request/response pair nyata
(`evidence_pair` / `diff_responses` — sudah ada di `analysis.py`, harus dipusatin),
bukan hitung jumlah evidence.

### GAP-011 — Tidak ada transfer kecerdasan antar target
**Bukti:** adaptive memory per host (`$WTH_HOME/adaptive/<host>.json`), self_improve
lesson global, tapi tidak ada "common lesson" yang dipromosikan antar-target.
**Fix:** normalisasi lesson: sinkronisasi + pull evaluasi similarity antar lesson
(kedua mode LLM-free: cosine embedding sederhana / keymatch),EDURE.
**Prioritas:** MEDIUM.

---

## D. GAP OPS/INFRA (minor tapi nyata)

### GAP-012 — `pip install` rusak (WU-208)
**Bukti:** konsol script hanya jalan dari checkout source.
**Fix:** packaging: entry_points + MANIFEST + wheel test.

### GAP-013 — API-intel 33 tools mati tanpa key, README tidak jujur
**Bukti:** `is_available` via `_api_key_present` — tanpa env/vault key, censys/shodan/vt/
github dsb semua `unavailable` silently.
**Fix:** `/intel keys add censys=...` self-service + /doctor mencetak yang mati
kenapa (bukan cuma "available=False").

### GAP-014 — Tool availability = `shutil.which` saja, parser bisa mismatch
**Bukti:** registry `_external_available` — tidak ada test formatter harware chain
agar JSON/CSV parser dapat valid data. Satu regex salah = garbek tools accent.
**Fix:** smoke test per tool (loop 3 sample dengan mock target dummy lokal) →
break/enable flags di user overrides registry (`recon_tools.yaml`).

### GAP-015 — 30 tool "manual/stub" tidak pernah bisa jalan
**Bukti:** kategori `manual` di catalog — tidak diwujudkan apapun.
**Fix:** tag regex string → pilih dihapus atau implementasi seminimal terapis.

### GAP-016 — LLM research backend DDG easy-blocked
**Bukti:** `wth_ai/research/engine.py` backend bawaan `duckduckgo-lite/html`
(scrape), dan kalau DDG nolak → gagal diam. Tidak ada fallback Exa/Perplexity.
**Fix:** backend berantai dengan failover + konfigurasi env API
(`EXA_API_KEY`/`SERPER_API_KEY`), plus cache hasil per query.

### GAP-017 — Rate limiter saya hanya `time.sleep`, tanpa concurrency
**Bukti:** `_throttle()` sleep seki; no-limits → direct skip. `autopilot` masih
sequential.
**Fix:** (opsional) per-domain token bucket + worker pool untuk
autopilot/recon kecil multi-target; silakan cek priority rendah.
---

## E. LOG PUTARAN EVALUASI

### R1 (7 Nov 2026) — baseline 8 kategori
- 346 tools catalog; aktual 104 available; 179 external hilang; 33 API mati
  tanpa key; 30 manual stub. Faktur akan dipetakan ke GAP-001/013/014/015.
- Adaptive: tipis (28 mutasi, GET bias; tidak ada body-schema/auth/WS mutation) — diserap
  ke GAP-002/005/006/009.
- Executor hanya 5 (auth, idor, race, ssrf, xss) — diserap ke GAP-009.
- verify_offline naif — GAP-010.
- Propose rule-based — GAP-008.
- Throttle sleep & autopilot sequential — GAP-017.
- Tidak ada sandbox docker — GAP-007.
- pip install rusak — GAP-012.
- Strix parity yang hilang: docker sandbox (GAP-007), API web research fallback
  (GAP-016), proxy intercept (GAP-003), browser intera body dettagli ≠
  `research browser` (baru disadari R2 — belum ada tool agent yang node interx;
  ved spillover).
- Prioritas roadmap yang diusulkan: (1) GAP-009 executor vuln diperluas;
  (2) GAP-014 + GAP-013 honesty tooling; (3) GAP-008 planner LLM hybrid;
  (4) GAP-007 sandbox; (5) GAP-012 packaging; (6) eval harness end-to-end.

### R2 (7 Nov 2026) — verifikasi mendalam (fakta baru)
- **OOB tervalidasi NYATA** — berangkat dari status hipsotea R1 ke fakta.
- `nuclei/sqlmap/hydra/wfuzz` binary terpasang tapi
  tersistem adapter nol — diserap GAP-001
  (sebelumnya gua curiga, sekarang fakta).
- `http_probe` TANPA cookie/proxy/multipart/redirect-visibility —
  fakta baru dari bridge.py, di-insert sebagai GAP-002..5.
- WebSocket gap baru — GAP-006.
- Browser `research browser` = nodriver+chromium njalan tapi TIDAK
  terintegrasi jadi tool hunting aktif hanya kita rekonos only.
  (gap spillover mungkin GAP-018 TBD: "browser jadi tool probe aktif")

### Catatan administratif
Semua GPAP/eval-status harus selalu di-post verifikasi dengan live smoke
sebelum dipindah status hipsoteasil→verifikatif. Rule: tool crashes = malam
malam auto eval, mgunakan vy SAPGA. High must high-signal ( hanya digithub.
---

### R3 (7 Nov 2026) — KAMPANYE PERBAIKAN (greenlit "gas aja")
- **FIX GAP-002** cookie jar persisten di `http_probe` (`bridge._http()` = requests.Session
  hidup antar probe; login sekali, probe berikutnya membawa cookie). Terbukti live:
  portal echo `session=abc123` dari hop kedua; `session_clear=True` memutus jar.
- **FIX GAP-003** proxy: kwarg `proxy` diterapkan ke both http/https (intercept-style).
- **FIX GAP-004** redirect hop capture: `allow_redirects=False` + manual hop
  (≤8), tiap hop ditulis `#n STATUS asal -> tujuan`, chain STOP bila redirect keluar
  scope; `redirect_chain` field baru di `TerminalResult`/`broker.ExecutionResult`
  dan dipropagasi broker→dispatch_tool (pola sama seperti fix response_headers).
- **FIX GAP-005** multipart upload: `files={field: path}` di-op封 dengan `boundary=`
  real; file hilang fail-closed (BLOCKED "gagal dibuka").
- Terbukti live via smokelokal: 8 kasus (chain rel/abs, jar, STOP out-of-scope,
  multipart, no-file, no-follow 302). Regresi: tests/test_http_probe_session.py (8) +
  tests/test_http_probe_vault_credential_injection.py patch target `requests.Session.request`.
- **FIX GAP-009 (sebagian besar)**: 4 executor baru — SqliExecutor (error-marker 15
  engine + boolean-blind 1=1/1=2 + MySQL SLEEP(4) time-blind), SstiExecutor (5 engine
  contact: jinja/freemarker/thymeleaf/erb/ejs via komputasi motornya muncul, ekspresi
  mentah tidak), CmdiExecutor (`;id`/`$(id)`/backtick → regex `uid=…` ; `&ver`), PathTraversalExecutor
  (passwd/win.ini, plain+urlenc+filter-evade). Artworker: `@local vuln server` mew ` risks
  high/critical nyata. Terwire di `VULN_EXECUTORS` (15 alias) + `KNOWN_CLASSES` generator.
- **FIX GAP-001 (bagian)**: NucleiExecutor — `nuclei -json -silent -nc` diparse
  per-line; severity→risk (critical/high/medium/low), greg counts + unparsed_lines.
  Sisa GAP-001: sqlmap/hydra belum ada adapter.
- Suite: **1469 passed** (1453 → 1469, hanya tambah; nol regressi).

### CES R4 yang masih terbuka di backlog:
- GAP-001 sisanya (sqlmap/hydra adapter), GAP-006 WebSocket, GAP-007 sandbox docker,
  GAP-008 propose LLM hybrid, GAP-010 verify deterministik pakai evidence_pair,
  GAP-011 transfer antar target, GAP-012 packaging pip, GAP-016 fallback search engine,
  GAP-017 concurrency worker pool, GAP-018 browser probe aktif, GAP-009 sisanya
  (deserialization / prototype pollution / GraphQL deep).

### R4 (7 Nov 2026) — ARENA REGRESI + CLEAN-ROOM (greenlit) 
- **NEW GAP-019 → FIX: Arena regression harness** (`wth_ai/arena/`)
  - targets.py: satu server vuln lokal (stdlib, ephemeral, 127.0.0.1) dengan 11
    endpoint rentan deterministik (sqli error+boolean, ssti, cmdi, traversal,
    xss, ssrf content-proof, idor object-swap, auth JWT alg-none, race burst,
    nuclei selftest template).
  - runner.py: `ArenaCase(expect=...)` — PASS hanya bila ≥1 evidence mengandung
    substring (case-insensitive); SKIP bila binary eksternal tidak ada; FAIL
    bila executor gagal menemukan. Laporan + JSON di `$WTH_HOME/arena/last_run.json`.
  - Hasil smoke: **11/11 PASS** (termasuk nuclei 1 temuan via template lokal).
  - **Anti-overclaim gate**: `tests/test_arena_harness.py` — semua kasus non-skip
    WAJIB PASS di tiap run CI. Ini patokan kepinteran, bukan narasi.
  - CLI: `wth arena run` (`--fast` skip nuclei) — verified.
- **NEW GAP-020 → FIX: Clean-room runner tanpa-Docker** (`wth_ai/executor/cleanroom.py`)
  - Deklarasi jujur: BUKAN jail kernel (tanpa container/ns). Yang diisolasi:
    cwd/HOME/TMPDIR throwaway di `$WTH_HOME/cleanroom/<id>`, env `WTH_*`
    dipangkas, opsi venv fresh.
  - Terwire: `bridge.run_shell(cleanroom=True)` + broker pass-through +
    **`python_runtime` sekarang default cleanroom=True** (kode agent-written
    tidak lagi menyentuh HOME/config host).
  - 5 test baru (isolasi env/ctwd/HOME, timeout→124, cleanup). 
- Suite: **1475 passed** (1469 → 1475; nol regressi; arena masuk sebagai test tetap).

### R5 (9 Nov 2026) — GAP-012 PACKAGING PIP (tertutup, terverifikasi build+install)
- **FIX GAP-012 / WU-208 / ARCH-02**: implementasi CLI 1.823 baris dipindah dari
  root `wth-main.py` (tidak ikut wheel) ke **`wth_ai/main_cli.py`** (kode paket).
  Root `wth-main.py` kini shim tipis untuk source checkout/Docker/PyInstaller/test;
  satu sumber kebenaran, tidak ada salinan ganda.
- **`wth_ai/resources.py`**: resolver data tunggal — paket dulu, fallback source.
  Menghapus `parents[2]/prompts` (path source-tree yang mustahil ada di wheel).
- **Prompts ikut wheel**: `wth_ai/prompts/*.txt` (9 file) + anti-drift test yang
  membandingkan byte-identik dengan `prompts/` root.
- **package-data benar**: manifest.yaml, recon_rules.yaml, brain/rules.yaml,
  arena/templates/*.yaml semua masuk wheel (kunci subpaket TOML dikutip penuh).
- **`setup.py` dihapus** (v2.0.0 vs pyproject v1.0.0 = konflik metadata);
  pyproject satu-satunya sumber; versi tetap 1.0.0 sesuai startup message/tes.
- **Entry point**: source checkout → root launcher; wheel → `wth_ai/main_cli.py`;
  tidak pernah `chdir` ke site-packages; pesan error tetap actionable.
- **TUI lazy**: `wth_ai/cli/tui/__init__.py` pakai PEP 562; `import wth_ai.cli`
  tidak lagi memaksa `textual` (optional extra) — install dasar berfungsi.
- **Verifikasi nyata**: `pip wheel` sukses; venv Python 3.13 bersih + install
  wheel; dari cwd `/tmp`: `wth --version` = 1.0.0, `wth-ai --version` = 1.0.0,
  `wth arena sqli_error` **PASS**, `load_prompt(supervisor)` = 8.858 chars dari
  `site-packages/wth_ai/prompts` (bukan file repo).
- 13 tes baru (`tests/test_packaging_gap012.py`), suite: **1488 passed**
  (1475 → 1488; nol regressi).

### R6 (9 Nov 2026) — GAP-008 PRIOR BAYESIAN (deterministik, terverifikasi live)
- **FIX GAP-008 (scope yang disetujui: pengganti deterministik aturan statis)**:
  modul baru `wth_ai/hunt/priors.py` — sinyal (param, path, status, header,
  cuplikan body, temuan) diekstrak jadi fitur; posterior naive Bayes dihitung
  offline dan dinormalisasi. Bukan LLM hybrid (itu tetap opsi terpisah, tidak
  diklaim); ini menghapus penyebab utama keluhan: sinyal non-rule = 0 usulan.
- `DEFAULT_PRIORS`/`LIKELIHOODS` dinyatakan jujur sebagai konstanta heuristik,
  bukan probabilitas terukur; bobot hasil evaluasi (`eval/weights.json`) tetap
  mengali prioritas. Override `$WTH_HOME/eval/priors.json` didukung.
- Terintegrasi di `propose_next_tests` (temuan aturan → sinyal → Bayesian;
  dedupe method+URL; prioritas Bayesian sengaja < aturan temuan langsung).
  Autopilot kini menyertakan `response_headers` di request pair sebagai fitur.
- **Bukti live**: 2 pair (`/sqli?id=1` 200; `/sqli?id=1'` 500 error SQL) →
  usulan `sqli` muncul; dieksekusi ke server arena → **500 "you have an error
  in your sql syntax"** (bukti nyata, bukan narasi).
- 8 tes baru (`tests/test_bayesian_prior.py`): normalisasi posterior,
  sinyal non-rule menghasilkan usulan, prioritas, override config, header.
- Suite: **1496 passed** (1488 → 1496; nol regressi).

### R7 (9 Nov 2026) — STATE + NORMALIZER + VERIFIER DETERMINISTIK (inti GAP-010)
- **NEW**: `wth_ai/state/normalizer.py` — `Observation` ringkas (status, panjang,
  fingerprint body, header penting; cookie disimpan sebagai NAMA saja), `diff_observations`
  deterministik, normalisasi dari `TerminalResult`/pair. Body penuh tidak masuk state.
- **NEW**: `wth_ai/state/engagement_state.py` — `EngagementState`: observasi vs baseline
  per endpoint (id numerik/UUID dinormalisasi), anomali, hipotesis + posterior, verdict,
  metrik (`probes`, `verified`, `false_positives`, `needs_evidence`, `wasted_probes`,
  `turns_to_first_evidence`, `chain_depth`), konteks ringkas, persistensi atomik
  `$WTH_HOME/state/<engagement>.json`.
- **NEW**: `wth_ai/verify/deterministic.py` — aturan per kelas (sqli error/time,
  xss mentah vs ter-encode, ssti hasil evaluasi, cmdi uid/gid, traversal passwd,
  ssrf marker internal, idor diff objek JSON, auth 401/403→200, open-redirect
  Location eksternal). Prinsip: `verified` hanya dengan sinyal konkret; tanpa sinyal
  = `needs_more_evidence` (bukan false positive); `false_positive` hanya bila ada
  kontradiksi (payload ter-encode, objek identik, akses tetap ditolak).
- **Terintegrasi**: `VerifierAgent.verify_evidence()` + `verify_offline(pairs=..., vuln_class=...)`;
  `loop/engagement_loop.py` meneruskan pasangan task + tipe evidence schema;
  `run_autopilot(engagement=...)` mencatat observasi; `/autopilot` orchestrator
  membuat/menyimpan state per sesi.
- **Arena chain**: endpoint `/admin/export` (aktor dari parameter) ditambahkan HANYA
  untuk skenario rantai, bukan kasus terisolasi. Test `tests/test_chained_engagement.py`:
  baseline `/api/user/2` → probe `/api/user/1` (IDOR, email admin bocor) → baseline
  aktor guest 403 → probe aktor admin 200 (export). Kedua langkah diverifikasi
  deterministik; `chain_depth=2`, `turns_to_first_evidence=2`, `wasted_probes=0`,
  state bertahan setelah reload.
- **Tes**: `tests/test_engagement_state.py` (10), `tests/test_deterministic_verifier.py` (24),
  `tests/test_chained_engagement.py` (2). Suite: **1532 passed** (1496 → 1532; nol regressi).
- **Batas jujur (belum diklaim)**: (1) `EngagementState` baru terpakai di jalur autopilot,
  belum di `_execute_next`/`_verify` orchestrator; (2) `Finding` belum punya field
  `vuln_class`, verifier menginferensi dari title/description atau hint pemanggil;
  (3) di `engagement_loop`, jalur LLM masih didahulukan saat client tersedia —
  verifier deterministik baru gate utama saat LLM tidak ada / test mode.
  GAP-010 inti selesai; "deterministic-first saat LLM tersedia" tetap backlog.

### R8 (9 Nov 2026) — DETERMINISTIC-FIRST + VULN_CLASS + CHAIN TEMPLATES
- **Deterministic-first**: `VerifierAgent.verify_with_evidence()` menjadi gate di
  tiga jalur nyata (`loop/engagement_loop.py`, `Orchestrator._verify`,
  `supervisor_dispatch.verify_finding`). `verified`/`false_positive` dari aturan
  konkret TIDAK bisa ditimpa LLM; LLM hanya dipanggil untuk `needs_more_evidence`.
  Tanpa LLM, jatuh ke `verify_offline` dengan pasangan bukti yang sama.
- **Finding berbasis kelas**: `Finding` kini punya `vuln_class` + `task_id`
  (persisten di `to_dict`, dibaca `FindingsDB.add_finding`). Pipeline evidence
  (`to_findings_candidates(..., task_id=...)`) mengisi keduanya, sehingga vault
  bisa mencari pasangan baseline/probe tanpa menebak dari judul.
- **NEW `wth_ai/hunt/chains.py`**: template rantai yang membaca URL/parameter
  nyata dari pasangan bukti — IDOR→enumerasi objek (path/query), SQLi→versi DB
  + `LOAD_FILE`, SSRF→metadata AWS/port lokal, traversal→hostname/environment,
  open redirect→tujuan eksternal + OIDC discovery, auth→permukaan admin.
  Terintegrasi di `propose_next_tests` setelah aturan langsung, sebelum Bayesian;
  prioritas di bawah aturan temuan, dedupe URL+metode.
- **Bukti**: 12 tes `tests/test_chain_templates.py` + 4 tes verifier-first +
  tes loop penuh (bukti IDOR → proposal `chain:idor` → request HTTP nyata ke
  arena → observasi state). Full suite: **1549 passed** (1532 → 1549; nol regressi).
- **Batas jujur**: chain `IDOR→ATO` penuh belum ada (yang ada enumerasi objek,
  bukan pengambilalihan akun); `XSS→CSRF` dan `redirect→OAuth token theft`
  belum bisa diverifikasi tanpa browser/flow OAuth nyata; chain memakai heuristik
  pasangan terakhir bila teks bukti tidak cocok dengan body.

### R9 (9 Nov 2026) — STATE DI SEMUA JALUR + SINTESIS PAYLOAD DARI RESPONS
- **State tidak lagi hanya autopilot**: `Orchestrator.record_engagement_pairs()`
  dipanggil di `_execute_next` (jalur single dan run-all) dan sekarang juga di
  `loop/engagement_loop.py` setiap kali pasangan executor disimpan ke vault.
  `_engagement_lock` melindungi state; kegagalan pencatatan tidak mematikan hunt.
- **Konsistensi generator**: `KNOWN_CLASSES` sebelumnya menolak kelas yang punya
  executor nyata (`xss`, `ssrf`, `idor`, `auth`, `open_redirect`, dst). Sekarang
  kelas executor/katalog diterima; ada tes yang mengunci konsistensi ini.
- **NEW `wth_ai/hunt/synthesis.py`**: payload disusun dari fakta respons, bukan
  sekadar memilih entri katalog:
  - `ServerProfile` membaca DBMS dari error SQL, framework dari header/body,
    OS hint, konteks pantulan (html/attr_dq/attr_sq/js/encoded), kedalaman path,
    tipe body (JSON/form/XML), dan vendor WAF.
  - Grammar per kelas: SQLi dialek MySQL/Postgres/MSSQL/SQLite/Oracle (time,
    error, union/boolean), XSS breakout sesuai konteks pantulan, SSTI per engine,
    CMDi separator + `id`/`whoami`, traversal dengan kedalaman nyata + encoding +
    filter bypass + wrapper PHP, SSRF varian loopback/hex/decimal/metadata,
    open redirect parser variants, XXE, dan NoSQL operator injection pada body JSON.
  - `synthesize_next_tests()` mengubah sinyal respons menjadi `NextTest` dan
    terintegrasi di `propose_next_tests` (setelah chain, sebelum Bayesian), jadi
    autopilot benar-benar dapat mengeksekusi kandidat sintesis.
  - `synthesize_with_llm()` adalah hook eksplisit: profil respons masuk ke goal
    LLM, hasil tetap divalidasi `generator.validate_generated()`; tidak dipanggil
    otomatis oleh planner deterministik.
- **Tes**: `tests/test_payload_synthesis.py` (11) — deteksi MySQL/reflection,
  dialek SQLi, breakout XSS, kedalaman traversal, CMDi Windows, NoSQL JSON,
  XXE, sinyal→NextTest, integrasi planner, dan jalur LLM dengan router palsu.
- **Suite**: **1561 passed** (1550 → 1561; nol regressi).
- **Batas jujur**: sintesis adalah kandidat, BUKAN bukti; verdict tetap milik
  `verify/deterministic.py` setelah probe nyata. Loop retry/evolusi belum ada:
  kandidat yang gagal belum otomatis dimutasi dari delta respons. Kelas dengan
  executor first-class tetap 10; XXE/NoSQL/deserialization/prototype pollution/
  GraphQL/CSRF/smuggling/WebSocket baru tersedia sebagai kandidat sintesis/
  katalog atau lewat tool umum, belum punya executor+verifier deterministik
  end-to-end. Nuclei tetap bergantung template yang terpasang di host.

### R10 (9 Nov 2026) — PIPELINE PROBE: EXECUTOR JADI ADAPTER, BUKAN MONOLIT
- **Masalah yang dikoreksi**: executor lama mencampur empat tanggung jawab —
  menemukan parameter, memilih payload, mengirim request, dan menafsirkan hasil.
  Akibatnya kualitas AI dibatasi kualitas executor, dan tiap kelas menyalin
  boilerplate HTTP sendiri.
- **NEW `wth_ai/pipeline/`**:
  - `models.py`: `Surface` (endpoint + SEMUA parameter query/JSON/form),
    `RequestSpec`, `ProbePlan`, `ProbeOutcome`, `TechniqueResult`.
  - `session.py`: `ProbeSession` — satu transport; baseline disimpan sekali;
    probe menghasilkan observasi + diff deterministik; rate limit dihormati.
  - `techniques.py`: `SynthesizedTechnique` (rencana dari `hunt/synthesis.py`,
    verdict dari `verify/deterministic.py`) + `SqliTechnique` (kontrol boolean
    true/false + time-based MySQL/Postgres/MSSQL).
  - `executor.py`: adapter `execute_pipeline()` yang mengubah hasil pipeline
    menjadi `ExecutorResult` + pasangan bukti baseline/probe.
- **Executor dimigrasikan**: SQLi, XSS, SSTI, CMDi, path traversal sekarang
  hanya adapter ke pipeline. SSRF/IDOR/auth/race/nuclei tetap legacy karena
  butuh OOB callback, multi-role, atau burst paralel.
- **Bukan sekadar satu parameter**: `Surface.from_target()` membaca seluruh
  query param dan key body JSON/form; `ExecutorTask` kini punya field opsional
  `method`/`headers`/`body` (backward-compatible), sehingga pipeline bisa
  menguji POST JSON, bukan hanya GET.
- **Bukti**: `tests/test_pipeline_probe.py` (5) — surface semua param, JSON body,
  baseline/diff dengan sender palsu, SQLi error di body JSON, dan no-param
  informasional. `wth arena --fast`: **10/10 PASS** (sqli error/boolean, ssti,
  cmdi, traversal, xss, ssrf, idor, auth, race). Full suite:
  **1566 passed** (1561 → 1566; nol regressi).
- **Batas jujur**: pipeline masih sekuensial per endpoint dan menjalankan semua
  probe yang direncanakan (belum ada scheduler biaya/informasi); SSRF/IDOR/
  auth/race belum dimigrasikan; surface discovery belum membaca form/HTML/JS
  dari halaman (baru dari URL/body task); `ProbeSession` produksi memakai
  `requests` langsung, sedangkan gate scope/permission tetap di manager.

### R11 (10 Nov 2026) — SCHEDULER + AI PIPELINE PRIMITIVES + IDOR/AUTH MIGRATION
- **Scheduler**: `pipeline/runner.py` (`run_technique`, `PipelineRun`) memberi
  baseline sekali, anggaran probe, deadline, early-stop, hitung `skipped`, dan
  pasangan bukti seragam. `Technique` Protocol menambah `requires_parameter`,
  `probe_budget`, `early_verified`; `SqliTechnique` tetap tidak early-stop pada
  error SQL agar kontrol boolean selalu berjalan (hanya time delay >=2s).
- **Primitif AI**: tool `pipeline_surface` (inventaris parameter tanpa request)
  dan `pipeline_probe` (scope/authorized/permission gate; setiap probe lewat
  transport terminal teraudit `http_probe`; hasil + transcript ID dikembalikan;
  pasangan disimpan ke `state.metadata["request_pairs"]`). Tersedia untuk aktor
  planner/executor/recon.
- **Migrasi baru**: IDOR object-swap tanpa akun (`/api/user/2` → `/api/user/3`,
  path-only didukung) dan JWT `alg:none` forge. Login/enumerasi/default-credential
  tetap di executor karena butuh urutan request + rate-limit login. Open redirect
  memakai `follow_redirects=False` agar `Location` dibaca, bukan diikuti.
- **Transport**: `ProbeSession` memakai `requests.get/post` untuk GET/POST
  (kompatibel dengan monkeypatch scope/credential yang ada), `Session` untuk
  method lain.
- **Bukti**: `tests/test_pipeline_probe.py` 8 tes, `tests/test_pipeline_tools.py`
  8 tes, full suite **1577 passed** (1566 → 1577; nol regressi),
  `wth arena --fast` **10/10 PASS**.
- **Batas jujur**: SSRF dan race masih legacy (SSRF butuh OOB/dynamic marker;
  race butuh burst paralel yang belum ada di `ProbeSession`); pipeline masih
  sekuensial; registry teknik AI belum mencakup ssrf/race; surface discovery
  belum membaca form/HTML/JS/OpenAPI; `pipeline_probe` menyimpan seluruh pairs
  tanpa batas; full LLM-driven external hunt belum diverifikasi.

### R12 (10 Nov 2026) — SURFACE DISCOVERY DARI HALAMAN NYATA
- **NEW `wth_ai/pipeline/discovery.py`**: parser stdlib-only (tanpa browser/bs4)
  yang mengubah satu halaman menjadi kandidat `Surface`:
  - form GET/POST (action relatif/absolute, input, select, textarea, checkbox/radio
    checked, submit/file dibuang, multipart dilewati);
  - link same-origin (asset/external dibuang, fragmen dibuang);
  - endpoint inline-JS (`fetch`, `axios.get/post`, `XMLHttpRequest.open`, dan path
    `/api|/rest|/graphql|/admin`), dengan metode GET/POST yang sesuai.
  - Ranking form > endpoint JS berparameter > link; dedupe by method+URL+nama param;
    batas `max_surfaces`; `<base href>` dihormati.
- **Tool AI `pipeline_discover`**: fetch SATU halaman seed lewat `http_probe`
  teraudit (scope/authorized/permission), kembalikan kandidat + transcript ID,
  simpan ke `state.metadata["discovered_surfaces"]` (cap 200). Tersedia untuk
  planner/executor/recon.
- **Executor auto-discovery**: bila target tidak punya query/JSON/form dan
  technique memang butuh parameter, executor mengambil halaman seed, menemukan
  form/link/JS berparameter, lalu memprobe surface pertama itu. Bila seed gagal
  atau tidak ada kandidat, hasil tetap informasional `success`/low, bukan klaim.
- **Bukti**: 4 tes parser + 1 tes executor auto-discovery + 1 tes tool =
  6 tes baru; full suite **1583 passed** (1577 → 1583; nol regressi),
  `wth arena --fast` **10/10 PASS**.
- **Batas jujur**: JS eksternal (`<script src>`) belum difetch; OpenAPI/sitemap/
  robots belum dibaca; form multipart/file upload dilewati; tidak ada browser
  untuk JS SPA dinamis; auto-discovery memilih kandidat pertama (bisa form logout/
  search yang salah) sehingga tool AI tetap perlu memilih surface; hasil discovery
  adalah kandidat, bukan temuan.

### R13 (10 Nov 2026) — OPENAPI/SITEMAP DISCOVERY (BUKAN CUMA HTML)
- **`pipeline/discovery.py`** sekarang punya dispatcher `discover_document()`:
  dokumen JSON OpenAPI/Swagger → `discover_from_openapi()`, XML sitemap →
  `discover_from_sitemap()`, selain itu HTML/JS seperti R12.
- **OpenAPI**: membaca `servers`/`host`/`basePath`, seluruh path+method,
  path/query parameter dengan contoh/default/enum/tipe, `requestBody` JSON atau
  form-urlencoded; parameter path yang belum terisi diganti `1`; surface
  same-origin saja (server eksternal tidak diteruskan ke scope probe).
- **Sitemap**: mengekstrak `<loc>` same-origin, membuang asset, dedupe/ranking.
- **Tool `pipeline_discover`** memakai `discover_document()` sehingga seed bisa
  berupa `/openapi.json` atau `/sitemap.xml`, bukan hanya halaman HTML.
- **Executor auto-discovery** juga memakai `discover_document()`; seed OpenAPI
  dapat langsung menghasilkan endpoint berparameter untuk diprobe.
- **Bukti**: 2 tes parser baru + 1 tes tool OpenAPI + 1 tes executor OpenAPI;
  full suite **1587 passed** (1583 → 1587; nol regressi),
  `wth arena --fast` **10/10 PASS**.
- **Batas jujur**: OpenAPI YAML belum didukung (butuh dependency); `$ref`
  kompleks/`allOf` belum di-resolve; auth scheme/header dari `security` belum
  diterapkan; sitemap bukan bukti endpoint punya parameter; semua hasil tetap
  kandidat dan harus lewat baseline/probe deterministik.

### R14 (10 Nov 2026) — RACE BURST MASUK PIPELINE (CUSTOM EXECUTION HOOK)
- **`ProbeSession.burst()`**: kirim N `ProbePlan` identik secara paralel
  (`ThreadPoolExecutor`, max 20), tetap memakai sender/transport yang sama
  (terminal `http_probe` di tool AI, `requests` di executor) dan baseline yang
  sama. `probe()` sekuensial tetap default untuk kelas lain.
- **`RaceTechnique`**: 20 plan burst; method dari `task.command_plan` bila ada,
  kalau tidak GET lalu fallback POST bila semua respons 404/405/0 (endpoint
  POST-only). Interpretasi: >1 respons 2xx = `verified`/high, plus hitung
  bentuk respons berbeda. Tidak ada klaim bila hanya satu yang menang.
- **Runner**: hook opsional `run_probes()` pada Technique; `run_technique`
  memakai hook bila ada, mencatat `skipped`, dan tetap menjalankan
  `interpret()` deterministik. Technique generik tidak berubah.
- **`RaceExecutor`** sekarang adapter `execute_pipeline(..., "race")`.
- **Tool AI**: `pipeline_probe` menerima `race`; default `max_probes` mengikuti
  `technique.probe_budget` (race=20, clamp 30).
- **Bukti**: tes race lama (GET→POST, kontrol single-success) tetap lulus;
  1 tes tool burst paralel; full suite **1588 passed** (1587 → 1588; nol
  regressi), `wth arena --fast` **10/10 PASS** (race evidence sekarang dari
  pipeline: `5/20 request POST paralel mengembalikan 2xx`).
- **Batas jujur**: burst bisa mengubah state (redeem/checkout) sehingga hanya
  boleh pada scope berizin; tidak ada idempotency/cleanup otomatis; 20 request
  adalah angka tetap, bukan adaptif; race masih satu metode/endpoint, belum
  multi-resource transaction; SSRF satu-satunya kelas executor yang belum
  dimigrasikan.

### R15 (10 Nov 2026) — SSRF PIPELINE: BUKTI KONTEN, BUKAN ERROR
- **Baseline khusus technique**: `run_technique` mendukung
  `technique.baseline_request()`; SSRF memakai baseline URL placeholder
  `http://127.0.0.1:1/wth-ssrf-probe` sehingga request pertama tetap probe
  (scope-guard lama tidak dilanggar), bukan halaman asli.
- **`SsrfTechnique`**:
  - Menemukan param URL-like (`url/redirect/next/callback/dest/target/uri/link`).
  - `run_probes()` mengambil halaman root target sebagai **reference**, mengekstrak
    penanda unik (title/h1/60 char), lalu meminta server mengambil root loopback
    via param tersebut.
  - **Verified hanya jika** penanda reference muncul di respons internal DAN
    tidak muncul di baseline. Ini menutup false-positive pada server yang
    mengabaikan parameter dan mengembalikan body yang sama.
  - Tanpa bukti: error 500/502/504 atau error jaringan internal hanya
    “Indikasi (lemah)”, risk medium; bukan temuan.
- **Kompatibilitas OOB**: `SsrfExecutor` tetap mempertahankan `_try_oob_callback`
  / `_try_mcp_oob` untuk wiring callback blind-SSRF, tetapi `execute()` sekarang
  adapter `execute_pipeline(..., "ssrf")`.
- **Tool AI**: `pipeline_probe` menerima `ssrf`; seluruh request tetap lewat
  `http_probe` teraudit.
- **Bukti**: tes SSRF lama (konten internal, scope-guard reachability, OOB
  wiring) lulus; 1 tes tool SSRF baru; full suite **1589 passed**
  (1588 → 1589; nol regressi), `wth arena --fast` **10/10 PASS**.
- **Batas jujur**: teknik memakai root loopback + port target; belum menjelajah
  metadata cloud/redirect chain/DNS rebinding; marker fallback bisa generik
  (karena itu baseline-difference wajib); OOB masih berupa hint/wiring, bukan
  hit otomatis di dalam technique; semua kelas executor kini pipeline, tetapi
  nuclei tetap tool terpisah dan bergantung template host.

### R16 (10 Nov 2026) — BLIND SSRF: OOB CALLBACK OTOMATIS
- **`SsrfTechnique._oob_outcome()`**: bila Callback Center lokal sedang hidup
  (`/oob start`), technique membuat token, mengirim URL listener lewat parameter,
  lalu polling hit maksimal 1 detik. Hit = `verified`/high dengan bukti
  `TERBUKTI (OOB): server target menghubungi callback kita — N hit ...`.
- **Kontrak listener tetap**: technique TIDAK pernah membuka listener sendiri;
  tanpa listener tidak ada request OOB tambahan (scope-guard `call_count <= 2`
  masih lulus). Callback loopback tidak dikirim ke target remote (tidak bisa
  dijangkau); hanya target loopback atau listener non-loopback.
- **Urutan**: reference root → probe internal (bila marker ada) → OOB hanya bila
  bukti konten belum ada. Server yang tidak memantulkan isi tetap bisa dibuktikan
  lewat hit listener.
- **Bukti**: tes blind SSRF baru (server fetch callback nyata) lulus; full suite
  **1590 passed**; `wth arena --fast` **10/10 PASS**.
- **Batas jujur**: polling 1 detik; callback asynchronous lebih lambat tidak
  ditunggu; listener harus sudah dikonfigurasi operator; token OOB tidak
  otomatis dijadikan transcript oleh technique (masih lewat `oob_check`).

### R17 (10 Nov 2026) — EXTERNAL JS + OPENAPI YAML
- **`discover_from_js()`**: parser JS mentah untuk `fetch`, `axios.get/post`,
  `XMLHttpRequest.open`, dan path `/api|/rest|/graphql|...`; dipakai untuk
  inline dan file eksternal.
- **`external_script_urls()`**: melaporkan `<script src>` same-origin (tanpa
  request); caller yang memutuskan fetch.
- **`pipeline_discover`**: fetch maksimal 5 file JS same-origin lewat
  `http_probe` teraudit, lalu gabungkan surface-nya; respons >512 KB dilewati.
- **Executor auto-discovery**: bila halaman tidak punya form/link/JS inline,
  fetch maksimal 3 file JS same-origin lewat `ProbeSession` sebelum menyimpulkan
  “tidak ada parameter”.
- **OpenAPI YAML**: `discover_from_openapi` menerima JSON atau YAML
  (`yaml.safe_load`); `discover_document` mendeteksi content-type YAML dan
  dokumen yang diawali `openapi:`/`swagger:`.
- **Bukti**: 2 tes discovery (JS mentah/external URL, YAML), 2 tes tool, 1 tes
  executor; full suite **1595 passed**; `wth arena --fast` **10/10 PASS**.
- **Batas jujur**: tidak mengeksekusi JavaScript, tidak mengikuti bundle
  dinamis/import graph, tidak fetch script cross-origin, `$ref` OpenAPI masih
  belum di-resolve, dan YAML hanya subset yang aman.

### R18 (10 Nov 2026) — OPENAPI LOCAL `$ref` RESOLUTION
- `_resolve_refs()` menyelesaikan JSON Pointer lokal (`#/components/...`) untuk
  parameters, schema, requestBody, dan struktur lain; sibling `$ref` dipertahankan.
- Siklus dipotong (`seen`) dan kedalaman dibatasi 12; `$ref` eksternal/URL TIDAK
  difetch — discovery tidak boleh keluar scope hanya karena isi dokumen.
- Bukti: 1 tes OpenAPI `$ref` (parameter path + schema body), full suite
  **1596 passed**, arena **10/10 PASS**.
- Batas: `$ref` eksternal dan `$dynamicRef` belum didukung; OpenAPI 3.1
  `webhooks` belum dipetakan.

### R19 (10 Nov 2026) — OOB HIT → TRANSCRIPT OTOMATIS
- `SsrfTechnique._oob_outcome()` sekarang menerima `transcripts` dari context dan
  memanggil `svc.record_as_transcript()` saat hit OOB; `transcript_id` disimpan
  di `plan.meta`, dan evidence menjadi
  `TERBUKTI (OOB): ...; bukti [TX:...]`.
- `pipeline_probe` mengoper `ctx.terminal.transcripts` + `ctx.state` ke runner,
  jadi jalur tool AI menghasilkan bukti callback yang bisa dikutip (TTST),
  bukan hanya teks hit.
- Bukti: 1 tes tool blind-SSRF OOB yang memeriksa `[TX:...]` dan transcript
  store; full suite **1597 passed**, arena **10/10 PASS**.
- Batas: executor tanpa terminal tetap hanya mencatat evidence teks; transcript
  otomatis saat ini khusus jalur `pipeline_probe`.

### R20 (10 Nov 2026) — JS IMPORT GRAPH (STATIS)
- `js_import_urls()` mengekstrak `import ... from`, `import()`, `require()`,
  dan `importScripts()`; hanya URL same-origin yang bisa di-resolve.
- Bare specifier (`lodash`, `node:fs`), URL eksternal, `data:`/`blob:`, dan aset
  non-JS (CSS/gambar/font/JSON) diabaikan.
- `pipeline_discover` memakai queue: 5 script langsung + import sampai total 12
  fetch teraudit; `imported_js_fetched` dilaporkan terpisah.
- Executor auto-discovery memakai queue yang sama (3 seed + import, maks 5 fetch).
- Bukti: 1 tes parser import, 1 tes tool (app.js → chunk.js → dua route), 1 tes
  executor; full suite **1600 passed**, arena **10/10 PASS**.
- Batas jujur: tidak mengeksekusi JS, tidak mengikuti dynamic import berbasis
  variabel/template, tidak fetch cross-origin, dan tidak membangun graph
  dependensi penuh (hanya breadth-first dangkal dengan cap).
