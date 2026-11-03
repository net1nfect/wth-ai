# 00 — Existing Architecture (Current State)

> Hasil riset langsung terhadap source code `wth_ai/` (bukan dokumentasi/asumsi), dikumpulkan lewat 3 jalur eksplorasi paralel + audit keamanan menyeluruh yang sudah selesai sebelumnya (`AUDIT_REPORT.md`/`SECURITY_AUDIT.md`/`ARCHITECTURE.md` di root repo). Dokumen ini adalah baseline "apa yang ada sekarang" sebelum gap analysis (`01-gap-analysis.md`) dan target architecture (`docs/architecture/`).

## 1. Entry Point & Dua "Produk" dalam Satu Repo

Entry point resmi `pyproject.toml` (`wth-ai = "wth_ai.cli.entry:main"`) **diketahui broken oleh maintainer sendiri** — `install.sh:53` punya komentar eksplisit *"Primary launcher — always uses wth-main.py (avoids broken entry imports)"*. Semua deployment nyata (`Dockerfile`, `docker/entrypoint.sh`, `install.sh`) memanggil `wth-main.py` (root, 1401 baris) sebagai launcher aktual. Kode legacy ("H1T4M-AI": `core/`, `agents/` root, `exploitation/`, `reconnaissance/`, `learning/`, `integration/`, `reporting/` root, `utils/`, ~2000 baris) terkonfirmasi orphaned — tidak direferensikan jalur produksi manapun, hanya diimpor 4 file test lama yang masih lolos CI (memberi false-confidence hijau untuk kode yang tidak pernah shipped).

## 2. Request/Command Lifecycle

```
wth-main.py (Click CLI: setup/vault/provider/model/profile/job/cron/gateway/recon/bot/...)
  tanpa subcommand → WTHOrchestrator.run_interactive()
    → cli/terminal_shell.py pilih TUI (Textual) atau plain REPL
      (TUI HANYA alternate renderer — WthTuiApp._execute_command() tetap panggil
       orch.handle_input(text) sama seperti REPL; tidak ada jalur dispatch terpisah)
```

`WTHOrchestrator.handle_input()` (`orchestrator.py:349-402`) — urutan prioritas deterministik:
1. Literal command (`/help`,`/quit`,`/stop`,`/new-session`,`/resume`,`/doctor`,`/status`,`/sessions`) — if/elif TERPISAH dari slash_router, dicek duluan.
2. `text.startswith("/")` → `slash_router.route()` — Python langsung, no LLM.
3. `scope_intent.py` regex heuristik ("target x.com", bare URL) → Python langsung, no LLM.
4. Fallback `SkillRouter.detect_intent()` (`skills/router.py:87-111`) — **regex/keyword, fungsional BINARY** (chat vs assess) meski nominal 3 label (`command` branch dead di titik ini karena `/` sudah ditangani di langkah 2). `chat` → `ChatAgent.respond()` (LLM role planner, tanpa tool-calling). Selainnya → `SupervisorAgent.run()` → `AgentToolLoop` (ReAct tool-calling).

**4 tingkat dispatch, bukan 1** — kompleksitas tersembunyi yang tidak terdokumentasi di README.

### Registry command — 3 sistem paralel, tidak sinkron
| Registry | Status | Fungsi |
|---|---|---|
| `slash_router.py::build_slash_router()` | **Aktif, satu-satunya yang dieksekusi** (~39 handler) | Dispatch nyata |
| `slash_commands.py::SlashCommandHandler` | **Dead code terkonfirmasi** (35 command, diinstansiasi `orchestrator.py:64` tapi tidak pernah dipanggil) | — |
| `platform/registry.py::PlatformRegistry` | Aktif (40 `PlatformCommand`) | `/help`, autocomplete TUI |

## 3. Agent Loop — 3 Loop Berbeda, Tanpa State Machine yang Benar-Benar Ditegakkan

| Loop | Lokasi | Dipakai oleh | State machine formal? |
|---|---|---|---|
| `AgentToolLoop` (ReAct) | `agents/tool_loop.py:24-111` | `SupervisorAgent` (setiap giliran "assess"), `ExecutorAgent._execute_with_tools`, `PlannerAgent._probe_phase` | Tidak ada — kontrol alur implisit dari `for`+`if` Python |
| `SupervisorAgent.run()` | `agents/supervisor.py:54-89` | Wrapper tipis di atas `AgentToolLoop` | Tidak ada |
| `EngagementLoop` (`/hunt`) | `loop/engagement_loop.py:19-167` | Hanya `/hunt`, memanggil agent method **langsung** (bypass `dispatch_supervisor_tool`) | **Ada** (`LoopState`) tapi **vestigial** |

`AgentToolLoop.run()`: `for iteration in range(max_iters)`, tiap iterasi 1 LLM call+tools; exit kalau tidak ada `tool_calls` (final answer, lewat `GroundingFirewall.validate_claims()`) atau `max_iters` habis (**hard cutoff** — semua observasi terkumpul dibuang, tanpa partial-salvage). Class-default `max_iters=8` **tidak pernah dipakai runtime** — nilai efektif SELALU 50 (`config.agent.max_iters`, dipakai `SupervisorAgent`/`ExecutorAgent`).

`LoopState` (`loop/state_machine.py:8-39`) punya `TRANSITIONS` table + guard `.transition()` yang **benar secara desain**, tapi **tidak pernah dipanggil** — 6 titik di `engagement_loop.py` (baris 43,69,106,117,158,161) langsung `self.sm.state = LoopState.X`, bypass total. `self.sm.state` juga tidak pernah dibaca di tempat lain. **Vestigial/write-only** — bukti konkret bahwa state machine yang "cuma didefinisikan" gagal jadi kontrol nyata di codebase ini.

**Bug baru ditemukan** (belum tercatat di audit keamanan): `supervisor_dispatch.py:178,206,237` mereferensikan `AssessmentPhase.PLANNING/.EXECUTION/.REPORTING` — **anggota ini TIDAK ADA** di enum asli (`models.py:81-89`, cuma punya `INTAKE/RECON/STATIC/DYNAMIC/HYPOTHESIS/VERIFY/REPORT/STOPPED`). `AttributeError` runtime tiap `plan_hunt`/`execute_task`/`generate_report` dipanggil via supervisor tool-loop — tertelan senyap oleh `except Exception` generik di `dispatch_supervisor_tool()`, padahal efek samping (task_queue terisi, task tereksekusi, report ter-build) **sudah terjadi** sebelum baris yang crash.

**3 phase-tracker paralel tidak sinkron**: `SessionState.current_phase:AssessmentPhase` (enum, buggy di atas), `SupervisorContext.current_phase:str="idle"` (string bebas, murni progress-bar UI hint via `notify_phase()`), `LoopState` (formal tapi vestigial).

**Historical fact (diverifikasi git)**: `wth_ai/planning/::TestPlanner` dan `wth_ai/executor/engine.py` (376 baris — rate-limiter per host, per-endpoint budget, retry/backoff, recovery mode setelah N error beruntun) **bukan dihapus** dari branch `agentic` — keduanya berasal dari branch paralel `cursor/fase6-platform-f3b2` yang berpisah sangat dini (merge-base sebelum kedua modul itu ditulis) dan tidak pernah di-merge. `agentic` adalah rewrite paralel yang mengambil arah ReAct tool-calling, bukan pipeline bertahap HTTP-only. Konsepnya (rate-limiter, recovery-mode) portable sebagai referensi desain; kodenya tidak plug-and-play (action-space beda: HTTP GET/HEAD murni vs tool-calling shell bebas).

## 4. Planner & Task Representation

- **`wth_ai/planner/` BUKAN planner asli** — cuma re-export `PlanningProfile` (11 preset tech-stack, lookup table teks statis).
- **Planner asli = `wth_ai/agents/planner.py`** (434 baris). `PlannerAgent.plan()`: kalau LLM support JSON mode → 1 call `chat_json_for_role` (Pydantic `PlanHypothesis`/`KilledHypothesis`/`PlanOutput`, terstruktur). Kalau tidak → fallback 3-tahap teks (Hunter→Critic 7-Question Gate→Feasibility), di-parse `parse_blocks()` — **regex heading `XXX_YYY:`+`- key: value`, rapuh, tanpa schema validation**, block salah format dilewati diam-diam.
- **`wth_ai/brain/`** — rule-based (bukan LLM): `HypothesisEngine.generate()` (rule YAML + rule bawaan-graph, `priority:int`, top-30), `ChainPlanner` (template exploit chain **linear statis** per vuln-class, bukan DAG).
- **`ExecutorTask`** (`models.py:223-261`) — dataclass FLAT, **tidak ada** field dependency/depends_on/blocks/priority. `stop_condition`/`success_criteria` string bebas **tidak pernah dibaca** kode manapun untuk keputusan (dead field fungsional). `SessionState.task_queue:list[dict]` — list datar, FIFO/by-id. **Tidak ada task DAG di mana pun di sistem** — satu-satunya "graph" adalah `AssetGraph` (attack-surface: host/endpoint/param), beda konsep total.
- Job (`jobs/orchestrator_bridge.py`) max **6 task per job hardcode** (`tasks_raw[:6]`).

## 5. Model/Provider Abstraction

- **Interface abstrak solid**: `BaseLLMProvider(ABC)` (`llm/providers/base.py`, 153 baris) — kontrak seragam, 3 implementasi (`OpenRouterProvider`, `OpenAICompatProvider` — 1 class untuk openai/anthropic/google/groq/9router/custom via base_url-swap, `OllamaProvider`) menutup 7 provider name.
- **Router**: `LLMProviderRouter` (`llm/router.py`, 439 baris) — `ROLE_NAMES` kanonik (planner/recon/executor/code/verifier/reporter/fallback), failover chain.
- **Bug wiring baru ditemukan**: `BaseLLMAgent._call()` (`agents/__init__.py:67-97`) terima parameter `model:str, backup:str` **tapi tidak pernah membacanya** — selalu `chat_for_role(role, ...)` dengan `role` default `"planner"`. Dari 6 call site `self._call(`: `VerifierAgent.verify()` dan `ExecutorAgent._enrich_task_from_llm/_parse_with_llm` **diam-diam SELALU pakai role "planner"** walau `/model set verifier ...` sudah diatur — parameter model/backup jadi dead argument di jalur itu. Hanya `ReportBuilderAgent.build()` yang benar (`role="reporter"` eksplisit). Jalur `ExecutorAgent._execute_with_tools()` (dominan, via `AgentToolLoop`) SUDAH benar.
- **Tidak ada canonical `ModelRequest`/`ModelResponse`** — response punya **3 bentuk berbeda hidup berdampingan**: `tuple[str,str]` (mayoritas), dict mentah tool-call (`chat_with_tools`), envelope `complete()` (paling "canonical" tapi PALING JARANG dipakai — cuma `/model test`).
- **Model capability registry adalah stub** — `get_context_length()`/`get_rate_limit_info()` tidak pernah di-override provider manapun, selalu `None`.
- **Model routing berbasis task/kompleksitas TIDAK ADA** — murni role-name hardcoded + config statis (`/model`,`/provider`,`/profile` — 7 preset builtin).
- **Error handling tidak konsisten** — `normalize_error()` cuma dipakai `complete()` (jarang dipakai); jalur chat utama pakai raw exception + string-matching manual `"timeout" in str(exc)` terduplikasi di 2-3 tempat.
- Legacy/orphan: `wth_ai/openrouter.py::OpenRouterClient` (root package, BUKAN folder legacy) — duplikat 1:1 `OpenRouterProvider`, tidak diimpor di mana pun.

## 6. Memory & Context

**5 mekanisme independen, storage berbeda, TIDAK saling referensi:**

| Mekanisme | Storage | Lifecycle | Isi |
|---|---|---|---|
| `ActionMemory` | JSON per-sesi | Session-ephemeral | Rolling 200 record, anti-loop (`should_block()`) |
| `EngagementMemory` | JSON per-sesi | Session-ephemeral | Docstring "knowledge graph", isi flat list (assets/routes/controllers/...) — **tumpang tindih dengan `AssetGraph`** |
| `SessionFtsIndex` (`fts.py`) | SQLite FTS5 | Lintas-sesi, global | Lexical recall, **agent-pull-only** via tool `session_search`, TIDAK auto-inject |
| `HuntMemoryLearning` (`learning.py`) | SQLite relasional | Lintas-sesi, global | Tabel `outcomes(tech_stack, vuln_class, success, ...)` |
| `MdContext` (`md_context.py`) | Flat Markdown | Global, human-authored | `MEMORY.md`/`USER.md`, dump penuh max 8000 char masing-masing |

**Context builder ada 2 jalur beda kualitas, DIPANGGIL DENGAN FREKUENSI SAMA** (sekali per giliran user, keduanya — bukan trade-off performa yang disengaja, melainkan drift organik): `SupervisorAgent._build_context_block()` (statis/recency-based) vs `WTHOrchestrator._assess_loop()` (lebih dinamis: tech-profile/keyword-match, learning SQL filter, graph top-N priority). **Tidak ada retrieval semantik/embedding di mana pun.**

**Session/checkpoint**: JSON overwrite penuh per save (bukan incremental/event-sourced), conversation dipotong permanen ke 50 turn terakhir (ada jalur recall sekunder via FTS kalau `index_turn()` dipanggil), tanpa file-locking. Resume BERFUNGSI (rehydrate semua sub-store dari `data/engagements/{session_id}/`).

## 7. Asset Graph (Attack-Surface, BUKAN Task Graph)

`NodeType` (8): HOST, ENDPOINT, PARAMETER, AUTH_ROLE, JS_BUNDLE, SECRET, TECH_STACK, AUTH_FLOW. `EdgeType` (7): CALLS, REQUIRES_AUTH, REDIRECTS_TO, PARAM_OF, OWNED_BY_ROLE, HOSTED_ON, DISCOVERED_IN. **Dari 7 edge type, cuma `HOSTED_ON`&`PARAM_OF` yang pernah dibuat** — `REQUIRES_AUTH` cuma dibaca tak pernah ditulis (fungsi selalu return kosong), 4 lainnya tidak dipakai sama sekali. `AUTH_ROLE`/`AUTH_FLOW` node type praktis tidak pernah terisi.

Storage: in-memory dict-node+list-edge, serialize UTUH ke 1 file JSON per sesi, index tunggal (`"type:label_lower"→node_id`, dedup doang). Query = linear scan Python.

**Ada representasi graph KEDUA yang duplikat/tidak sinkron**: `recon/engine.py::_write_asset_graph` nulis `normalized/asset_graph.json` terpisah per recon job — dict polos beda skema, cuma 1 edge string `"param_of"`.

## 8. Evidence & Findings

- **`Evidence` dataclass + `EvidencePipeline`** (`executor/evidence.py`) — classifier regex/keyword sederhana, id berprefix `EVD-`. **Bug baru ditemukan**: `to_findings_candidates()` (baris 94-108) membuang `Evidence.id`, cuma simpan `ev.content` (teks) ke finding-candidate — linkage Finding↔Evidence untuk jalur ini **nol**, bukan cuma "soft-key" seperti diduga awal.
- **`EvidenceVault`** (`evidence/vault.py`) — paling terstruktur, prefix id **`EV-`** (beda dari `EVD-` di atas — dua skema id tidak pernah bertemu), redaksi otomatis, `validate_schema()` cuma **substring containment** (`proof in json.dumps(e)`).
- **`ExecutorTask.evidence_schema:dict`** — format bebas, tidak divalidasi JSON-schema formal.
- **`FindingsDB`** — nama menyesatkan, docstring eksplisit "in-memory findings and hypotheses store", **tidak ada persistensi sendiri**, numpang penuh di `SessionState.findings`(list[dict]) yang di-flush manual tiap giliran.
- **`FindingState`** (8 nilai) — **hanya 3 dipakai** di kode (`CANDIDATE`/`VERIFIED`/`FALSE_POSITIVE`), 5 lainnya (`HYPOTHESIS`/`NEEDS_EVIDENCE`/`ACCEPTED_RISK`/`FIXED`/`RETEST_PASSED`) tidak pernah di-assign. `FindingsDB.update_finding_state()` **tanpa transition guard** — set state apa pun dari state apa pun.
- **`GroundingFirewall`** (`agents/grounding.py`, 74 baris) — regex `CLAIM_PATTERNS` vs `TX_REF`/`EV_REF` sitasi, `GroundingResult` FLAT per-blok-teks (bukan per-klaim). **Tidak ada dataclass `Claim`** dengan claim_id/evidence_refs/confidence numerik/verification_status. TAPI genuinely **reachable & dipakai** di `AgentToolLoop.run()` dan `VerifierAgent.verify()` (short-circuit hemat biaya) — **bukan documentation drift** untuk 2 titik itu. **TIDAK diterapkan** ke `ReportBuilderAgent.build()` (`return raw` langsung, celah sudah tercatat AGENT-03 di audit keamanan).

## 9. "Engagement"/Scope, Permission, Approval (ringkasan — detail lengkap di SECURITY_AUDIT.md)

`Scope` (`models.py:92-117`) — **value object mutable tunggal per sesi**: `target(single string), authorized:bool, in_scope, out_of_scope, rate_limit_rps, forbidden`. **Tidak ada id/name/expires_at/excluded_assets/status** — bukan entity. `set_scope()` OVERWRITE total (riwayat hilang). Satu `SessionState` = satu `scope` = satu target aktif.

`PermissionMode` (5 nilai: READ_ONLY_RECON/STANDARD_TESTING/ELEVATED_APPROVAL/FULL_LAB/UNRESTRICTED) + `MODE_TOOLS` flat allowlist. **Bug ganda** di `check_command()`: (1) `bash/sh/zsh` di-skip TOTAL dari allowlist check di semua mode; (2) sisa cek pakai substring match bukan token match. Mode `UNRESTRICTED` early-return SEBELUM denylist check.

**Approval — 4 mekanisme paralel independen** (temuan baru dari riset arsitektur ini, di luar bug "permanen" yang sudah tercatat audit): `SupervisorContext.approved_tasks` (set, `bool()` check → permanen per sesi), `WTHOrchestrator._approved_tasks`, `ApprovalGate` in-memory task-based, `ApprovalRegistry` file-based job-based. **2 loop eksekusi paralel** (`SupervisorAgent` ReAct modern vs `WTHOrchestrator._execute_next`/`ExecutorPool` lama) yang keduanya perlu diperbaiki. **`http_probe` tidak punya parameter `approved` sama sekali** (`terminal/bridge.py:113` — beda dari `run_shell` baris 84) — **diverifikasi ulang manual, akurat**.

`WTH_UNRESTRICTED` env var (default AKTIF di `Dockerfile`/`docker-compose.yaml`/`.env*.example`) — master switch 6+ call-site (`recon/engine.py`, `recon/registry.py`, `recon/planner.py`, `jobs/policy.py`, `supervisor_dispatch.py`+`config.py::auto_approve_unrestricted=True`, DAN **`profiles.py` preset `bug-bounty`/`ctf-aggressive`** — preset PALING relevan untuk use-case utama produk, membundel `unrestricted=True`+`require_authorized=False`+`rate_limit_rps=0` jadi satu pilihan).

`jobs/policy.py::PolicyEngine.evaluate()` — dead-code fallthrough, SELALU `return True, ""` di baris terakhir apa pun `safe_mode`/`is_unrestricted`.

`ExecuteRpcServer.execute()` — eksekusi Python arbitrer, validasi cuma string literal `"execute"`, bukan isi `req["code"]` nyata.

`ReconEngine`/recon tool runner adalah **jalur eksekusi subprocess KETIGA** independen dari `AgentTerminalBridge`/`ExecuteRpcServer`, tidak pernah lewat `PermissionManager.check_command()`.

**Reliability bug tambahan** (ditemukan riset ini): jalur streaming `shell_runner.py::run_shell_live` tidak pernah `kill()` child process saat `TimeoutExpired` (proses leak). `WorkerEngine._run_job()` — `with ThreadPoolExecutor(): future.result(timeout=)` — exit blok `with` block sampai thread selesai sendiri, worker TIDAK benar-benar lanjut ke job lain saat "timeout".

## 10. Recon Engine & Event Architecture

Registry ≥200 `ToolSpec`, `SafeToolRunner` solid (`shell=False`, argv list, env allowlist ketat, timeout wajib, redaksi output). Builtin Python fallback (dns/http/headers/tls/crawler/js/params/cloud) berfungsi tanpa tool eksternal.

`EventBus` (`events.py`) — pub/sub in-process SINKRON, `history:deque(maxlen=200)` (hilang saat proses exit, tanpa durability). Event recon (`recon.new_asset/new_endpoint/change_detected/report_ready`, `job.*` lifecycle) **terverifikasi benar-benar diimplementasikan** (bukan vaporware). Consumer: TUI, `integrations/manager.py` (fan-out Telegram/Discord/Webhook, **outbound-only**).

**"Realtime" sebenarnya POLLING BERLAPIS**: `wth recon watch` → `LocalCronTicker` polling 30 detik → `CronScheduler.tick()` cek interval (`MIN_INTERVAL_SECONDS=3600` hard floor + jitter) → enqueue job SQLite → `WorkerEngine` polling (`idle_sleep=1.0`) drain antrian → `ReconEngine`, diff vs baseline, emit event. Satu-satunya bagian durable = job queue SQLite, **bukan** event bus (mati bareng proses).

## 11. Yang Sudah Baik (Security Strengths — KEEP)

`shell_runner.py`/`tool_runner.py` (shell=False, argv list, env allowlist), `vault.py` (Fernet AES-128-CBC+HMAC, chmod 600, referensi-only), `EvidenceVault` (redaksi otomatis), RPC transport (Unix socket chmod 600, bukan network-facing), `BaseLLMProvider` interface abstrak, `ACTOR_TOOLS` filtering per-actor, `GroundingFirewall`/TTST (reachable & dipakai nyata), `EventBus` recon events (benar-benar terhubung), Docker non-root user, SQLite query 100% parameterized, `yaml.safe_load` konsisten tanpa pengecualian.
