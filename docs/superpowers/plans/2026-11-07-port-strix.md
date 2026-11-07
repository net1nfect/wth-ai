# Port Fitur Strix ke WTH-AI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` untuk eksekusi inline, atau `subagent-driven-development` untuk eksekusi per-task. Langkah memakai checkbox (`- [ ]`). Satu commit per task, test ditulis lebih dulu dan harus terlihat gagal.

**Goal:** Membawa empat kemampuan Strix yang belum ada di WTH-AI — SAST codebase, agen graph, MCP parity + proxy terstruktur, rekayasa konteks LLM + benchmark — tanpa merusak empat invarian repo (scope, egress allowlist, kill switch, gate veracity).

**Architecture:** Setiap fitur masuk sebagai paket baru di bawah `wth_ai/` dengan satu seam sempit ke mesin yang sudah ada: `LLMProviderRouter` untuk LLM, `AgentToolLoop` untuk tool call, `MCPClientManager` untuk MCP, `SandboxSpec` untuk eksekusi terkurung, `veracity`/`verify_gate` untuk status temuan. Tidak ada jalur kedua: fitur baru tidak boleh mengeksekusi proses, membuka socket, atau menulis status temuan di luar seam itu.

**Tech Stack:** Python 3.10+ (stdlib first), Click, httpx/requests yang sudah ada, docker via `wth_ai/containment.py`, opengrep image (eksternal, opsional), pytest.

**Spec:** `docs/superpowers/specs/2026-11-07-port-strix.md` (ringkasan sumber: `https://github.com/usestrix/strix` v1.7.0, `pyproject.toml`, `strix/tools/agents_graph/`, `strix/tools/mcp/`, `strix/llm/compaction.py`, `strix/llm/context_budget.py`, `containers/opengrep-rules/`). Bila kode dan plan berbeda, kode menang; perbaiki plan.

**Konstanta Strix yang sudah diverifikasi dari sumber (2026-11-07, bukan dari README):**

| Sumber | Nilai | Dipakai di task |
|---|---|---|
| `strix/llm/compaction.py:35` | `_TOOL_OUTPUT_MAX_CHARS = 2_000` | Task 4 |
| `strix/llm/compaction.py:36` | `_MIN_ITEMS_TO_COMPACT = 6` | Task 4 |
| `strix/llm/compaction.py:364` | `budget = max(context.keep_tokens, window - reserve)` | Task 4 |
| `strix/llm/compaction.py:234` | head+tail truncate (awal dan akhir dipertahankan, tengah dibuang) | Task 4 |
| `strix/llm/context_budget.py` | `_DEFAULT_OUTPUT_TOKENS = 8_192`; token count fallback ke `len(text.encode("utf-8"))` | Task 4 |
| `strix/config/tool_call_limits.py` | `TurnToolCallLimiter(limit)` membatasi **jumlah tool call dalam satu respons**, per `call_id`, sisa dibuang dan dilaporkan lewat `dropped` | Task 2 |

WTH-AI tidak memakai litellm: jendela konteks diambil dari `wth_ai/llm/model_catalog.py:116` (`context_length(model) -> int | None`), dan hitungan token memakai batas atas byte UTF-8 (deterministik, offline).

## Global Constraints

- **Empat invarian tidak boleh dilonggarkan.** `ScopeManager` (`wth_ai/scope_manager.py:20`), `EgressGuard.check` (`wth_ai/netguard.py:22`), `KillSwitch.activate` (`wth_ai/killswitch.py`), `verify_gate.apply_verification` (`wth_ai/verify_gate.py:82`) tetap satu-satunya penulis status temuan.
- **Temuan statis maksimal `candidate`.** SAST tidak punya request/response, jadi `veracity.assess_claim` tidak boleh mengubahnya menjadi `CONFIRMED`; jangan tambah jalan pintas.
- **Tidak ada `shell=True`.** Argv-list lewat `shell_runner.run_argv` (`wth_ai/shell_runner.py:37`) atau `SandboxSpec.docker_args` (`wth_ai/containment.py:38`).
- **Tidak ada dependency baru tanpa alasan tertulis.** Opengrep/Semgrep dan Caido adalah binary/service eksternal, bukan paket Python baru; kegagalannya harus `blocked` dengan pesan jelas, bukan traceback.
- **Komentar Bahasa Indonesia, identifier Inggris.** Test tidak boleh menyentuh internet; pakai fixture, fake server, monkeypatch.
- **Path operator lewat `_user_path()`**; penulis file baru menghormati `WTH_HOME`/`WTH_REPORT_DIR`.
- **Perubahan konstitusi (Phase 0) di-review terpisah.** `AGENTS.md` adalah dokumen pemilik repo; perubahan larangan harus berupa aturan berbatas dengan default aman, bukan penghapusan kalimat.

## Review Focus

- Apakah setiap fitur baru bisa dimatikan tanpa mengubah perilaku lama (`config` default `false`)?
- Apakah SAST bisa menghasilkan temuan tanpa scope card? (harus tidak.)
- Apakah agen graph mewarisi scope/mode/kill switch, atau membuka jalur akses paralel yang tidak tercatat?
- Apakah compaction bisa membuang referensi bukti (`[TX:...]`) sehingga bundel tidak bisa diverifikasi?
- Apakah MCP http memperkenalkan egress di luar allowlist?

---

## Phase 0 — Konstitusi dan kejujuran dokumen

### Task 1: Amandemen AGENTS.md (aturan berbatas, bukan penghapusan)

**Files:**
- Modify: `AGENTS.md` §1 (batas keras), §7 (yang sengaja tidak dibangun)
- Modify: `docs/COMPARISON.md` (tabel Strix v1.7 — tambah baris cloud/autofix/MCP/ChatGPT-auth)
- Test: manual review, tidak ada test otomatis

**Interfaces:** Tidak ada API. Hasil akhir: enam larangan berubah menjadi izin berbatas yang default-nya mati, masing-masing dengan satu paragraf alasan dan satu baris konsekuensi keamanan.

- [ ] **Step 1: Tulis ulang §1** menjadi dua bagian: "Batas keras (tidak pernah)" dan "Izin berbatas (default mati)".

  Batas keras yang **tetap**: authorized target only; recon non-destruktif; tanpa brute force/OTP bypass/eksfiltrasi; secret hanya candidate ter-masking; tidak ada eksekusi shell bebas.
  Izin berbatas yang **dibuka** (permintaan pemilik repo, 2026-11-07):

  1. **Dashboard lokal** — hanya `127.0.0.1`, hanya membaca bundel yang sudah ditulis, tanpa mutasi, token acak per proses, mati kecuali `--view`. Tidak ada multi-user, tidak ada TLS, tidak ada bind `0.0.0.0`.
  2. **Auto-fix PR** — hanya mode `full_lab` + `--destructive`, hanya repo target yang tercantum di scope card, patch ditulis ke worktree terpisah, wajib retest, **tidak pernah** `git push` otomatis; PR body berisi bundel bukti.
  3. **Cloud sync** — opt-in eksplisit dengan URL yang harus lolos `EgressGuard`; tidak ada default endpoint; tidak ada upload artefak target tanpa flag terpisah.
  4. **Updater** — verifikasi tanda tangan + allowlist host; gagal verifikasi = tolak, bukan fallback diam-diam.
  5. **Telemetry** — default mati, hanya agregat (jumlah run, durasi, exit code), tidak pernah target/host/temuan/secret.
  6. **Distributed worker** — proses lokal dalam satu mesin, antrean berbasis file dengan lock; tidak ada scheduler remote, tidak ada worker lintas jaringan.

- [ ] **Step 2: Tulis ulang §7** agar mencerminkan keputusan di atas: pindahkan enam item dari "sengaja tidak dibangun" ke "izin berbatas, default mati, belum diimplementasikan".
- [ ] **Step 3: Perbarui §2 peta modul** — bagian ini sudah usang (hanya menyebut redteam/kb/ot/recon/cli-tui; kenyataannya ~40 paket termasuk `agents/`, `llm/`, `hunt/`, `jobs/`, `gateway/`, `bots/`, `computer_use/`, `memory/`, `eval/`, `research/`, `plugins/`, `cron/`, `graph/`, `policy/`, `rpc/`, `mcp/`, `platform/`, `broker/`, `brain/`, `probe/`, `ctf/`, `intel/`).
- [ ] **Step 4: `docs/COMPARISON.md`** — tambah baris: cloud/REST, auto-fix PR, MCP auth, ChatGPT-subscription auth, dashboard. Setiap baris menyebut status WTH-AI apa adanya ("tidak ada" / "berbatas, default mati").
- [ ] **Step 5: Commit**

```bash
git add AGENTS.md docs/COMPARISON.md
git commit -m "docs: izin berbatas untuk fitur strix + peta modul akurat"
```

---

## Phase 1 — Rekayasa konteks LLM (tanpa dependency baru)

### Task 2: Batas tool-call per run

**Files:**
- Create: `wth_ai/llm/limits.py`
- Modify: `wth_ai/agents/tool_loop.py:24` (`AgentToolLoop`), `:177` (`_dispatch_tool`)
- Test: `tests/test_llm_tool_limits.py`

**Interfaces:** `ToolCallBudget(max_total: int = 40, max_per_tool: int = 12, max_in_response: int = 16)` dengan metode `check(tool: str) -> str | None` (mengembalikan pesan penolakan atau `None`) dan `spend(tool: str) -> None`; `filter_response_calls(calls: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]` membatasi jumlah tool call dalam satu respons (paritas `TurnToolCallLimiter` Strix: yang pertama dipertahankan, sisanya dibuang dan dihitung). `AgentToolLoop.__init__(..., budget: ToolCallBudget | None = None)`.

- [ ] **Step 1: Test gagal** — `ToolCallBudget(max_total=2)` menolak panggilan ketiga dengan pesan yang menyebut batas; `max_per_tool=1` menolak tool yang sama dua kali; `AgentToolLoop._dispatch_tool` mengembalikan string penolakan (bukan exception) saat anggaran habis.
- [ ] **Step 2: Jalankan, pastikan gagal** — `.venv/bin/python -m pytest tests/test_llm_tool_limits.py -q` → `ModuleNotFoundError: wth_ai.llm.limits`.
- [ ] **Step 3: Implementasi** — counter `collections.Counter`, pesan Bahasa Indonesia, `audit_log("tool_budget_exhausted", tool=..., total=...)` sekali per run.
- [ ] **Step 4: Jalankan, pastikan lulus** — termasuk `pytest tests -q -k tool_loop` untuk memastikan perilaku lama tidak berubah saat `budget=None`.
- [ ] **Step 5: Commit** — `feat: batas tool-call per run dan per tool`.

### Task 3: Deteksi panggilan duplikat dan tool tak dikenal

**Files:**
- Modify: `wth_ai/llm/limits.py`, `wth_ai/agents/tool_loop.py`
- Test: `tests/test_llm_tool_limits.py`

**Interfaces:** `ToolCallBudget.observe(tool: str, args: dict[str, Any]) -> str | None` memakai hash `(tool, json.dumps(args, sort_keys=True))`; duplikat ke-3 dalam satu run mengembalikan pesan "panggilan identik ke-N, ubah argumen atau lanjut". `AgentToolLoop` menangani tool yang tidak ada di `ToolRegistry` sebagai pesan recoverable (tidak menghentikan loop).

- [ ] **Step 1: Test gagal** — tiga panggilan identik → penolakan pada yang ketiga; tool tak dikenal → pesan "tool `x` tidak dikenal" dan loop lanjut ke iterasi berikutnya.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi.**
- [ ] **Step 4: Jalankan, pastikan lulus.**
- [ ] **Step 5: Commit** — `feat: pulihkan tool call duplikat dan tool tak dikenal`.

### Task 4: Compaction konteks yang tidak membuang bukti

**Files:**
- Create: `wth_ai/llm/compaction.py`
- Modify: `wth_ai/llm/router.py` (`chat_with_tools_for_role`), `wth_ai/agents/tool_loop.py:113` (`_chat_with_tools`)
- Test: `tests/test_llm_compaction.py`

**Interfaces:** `compact_messages(messages: list[dict[str, Any]], *, model: str | None = None, keep_last: int = 6) -> tuple[list[dict[str, Any]], int]` mengembalikan pesan baru dan jumlah pesan yang diringkas. Aturan (paritas Strix `compaction.py`): bila `len(messages) < 6` tidak ada compaction; anggaran = `max(4_000, context_length(model) - 4_000)` token, dihitung dari byte UTF-8 sebagai batas atas; hasil tool lama dipotong head+tail ke 2.000 karakter; pesan yang mengandung `[TX:` atau blok `evidence` **tidak pernah** dibuang isinya, hanya dipotong ke 4.000 karakter dengan penanda `…dipotong, bukti utuh di bundel`.

- [ ] **Step 1: Test gagal** — pesan dengan `[TX:abc]` tetap memuat `[TX:abc]` setelah compaction; `len` pesan turun; pesan sistem tidak pernah disentuh; dengan `messages` berisi 5 item tidak ada perubahan; tool output 10.000 karakter dipotong ke 2.000 (head+tail, bukan kepala saja).
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** — ringkas hasil tool lama menjadi satu baris `{"role":"tool",...}` dengan nama tool + jumlah byte, jangan pakai LLM untuk meringkas (deterministik dan offline); Strix memakai checkpoint-summary lewat model, kita sengaja tidak.
- [ ] **Step 4: Jalankan, pastikan lulus** + `pytest tests -q -k "agents or router"`.
- [ ] **Step 5: Commit** — `feat: compaction konteks yang mempertahankan referensi bukti`.

---

## Phase 2 — SAST codebase (gap terbesar)

### Task 5: Target berjenis `source`

**Files:**
- Modify: `wth_ai/targets/spec.py`, `wth_ai/scope_card.py`, `wth_ai/cli/wth-ai-main.py` (`scope`, `scan`)
- Test: `tests/test_target_source_scope.py`

**Interfaces:** `SourceTarget(path: Path, name: str)`; scope card menerima `kind="source"` dengan `path` absolut yang harus berada di bawah root repo operator; `scan --source DIR` berjalan hanya bila scope card memuat `kind=source` dan `authorized=true`.

- [ ] **Step 1: Test gagal** — `scope --source DIR` tanpa `--authorized` → exit 2 dengan pesan scope; path di luar `$HOME` → ditolak; scope card memuat `kind: source` dan hash isi direktori (sha256 daftar file relatif).
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** — tanpa jaringan; `_user_path()` untuk path operator.
- [ ] **Step 4: Jalankan, pastikan lulus.**
- [ ] **Step 5: Commit** — `feat: target source dengan scope card berjenis source`.

### Task 6: Runner opengrep di dalam sandbox

**Files:**
- Create: `wth_ai/sast/__init__.py`, `wth_ai/sast/opengrep.py`, `wth_ai/sast/rules/` (subset OWASP Top 10, YAML)
- Modify: `wth_ai/containment.py` bila perlu mount read-only tambahan
- Test: `tests/test_sast_opengrep.py` (fake `docker` via monkeypatch)

**Interfaces:** `run_opengrep(target: Path, *, rules_dir: Path, timeout_s: int = 300) -> list[dict[str, Any]]` memanggil `SandboxSpec(image="opengrep/opengrep:latest", extra_mounts=(f"{target}:/src:ro", f"{rules_dir}:/rules:ro"), network="none")` dan mem-parse output JSON opengrep. Docker/jaringan tidak tersedia → `SastUnavailable("opengrep tidak tersedia (docker)")`, bukan traceback.

- [ ] **Step 1: Test gagal** — fake `docker` mengembalikan JSON opengrep → daftar temuan dengan `rule_id`, `path`, `line`, `severity`; `network="none"` ada di argv; tanpa docker → `SastUnavailable`.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** — `shell=False`, timeout, argv-list; tolak path target di luar scope card.
- [ ] **Step 4: Jalankan, pastikan lulus.**
- [ ] **Step 5: Commit** — `feat: runner opengrep dalam sandbox tanpa jaringan`.

### Task 7: Normalisasi temuan statis ke model temuan

**Files:**
- Modify: `wth_ai/redteam/models.py` (field `origin: "dynamic" | "static"`), `wth_ai/sast/opengrep.py`, `wth_ai/redteam/artifacts_write.py`
- Test: `tests/test_sast_findings_static.py`

**Interfaces:** `to_finding(raw: dict[str, Any], *, target: SourceTarget) -> Finding` dengan `status="candidate"`, `evidence={"kind":"static","rule":...,"snippet":...}`, `origin="static"`. `veracity.assess_claim` menolak menaikkan temuan `origin="static"` ke `CONFIRMED` (test regresi).

- [ ] **Step 1: Test gagal** — temuan statis selalu `candidate`; `apply_verification` dengan `verdict="confirmed"` pada temuan statis tetap `candidate` dan menulis alasan `static-no-runtime-evidence`.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** — jangan ubah jalur dinamis; tambah cabang eksplisit.
- [ ] **Step 4: Jalankan, pastikan lulus** + `pytest tests -q -k veracity`.
- [ ] **Step 5: Commit** — `feat: temuan statis masuk sebagai candidate dengan bukti snippet`.

---

## Phase 3 — MCP parity dan proxy terstruktur

### Task 8: Transport HTTP (streamable) + bearer dari vault

**Files:**
- Modify: `wth_ai/mcp/config.py` (`MCPServerSpec`: `transport: "stdio" | "http"`, `url`, `headers`), `wth_ai/mcp/manager.py:114` (`MCPClientManager`)
- Test: `tests/test_mcp_http_transport.py` (server HTTP lokal via `http.server` di thread)

**Interfaces:** `_HttpMCPConnection(name, spec)` dengan `start()`, `stop()`, `_rpc(method, params, timeout)`, `call_tool(tool, args) -> MCPToolResult`; header `Authorization: Bearer <vault-ref>` di-resolve `_resolve_headers` yang sudah ada. Setiap request melewati `EgressGuard.check(host)`.

- [ ] **Step 1: Test gagal** — manifest `{"transport":"http","url":"http://127.0.0.1:PORT/mcp", "headers":{"Authorization":"vault:x"}}` → `list_tools` mengembalikan tool; host di luar allowlist → `EgressDenied`.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** — JSON-RPC over HTTP, timeout per request, `Accept: application/json, text/event-stream`.
- [ ] **Step 4: Jalankan, pastikan lulus.**
- [ ] **Step 5: Commit** — `feat: transport MCP http dengan bearer dari vault`.

### Task 9: Namespacing, allowlist, dan anggaran panggilan per server

**Files:**
- Modify: `wth_ai/mcp/manager.py:159` (`call_tool`), `:170` (gate `allowed_tools`), `wth_ai/config.py` (`mcp.policy`)
- Test: `tests/test_mcp_policy.py`

**Interfaces:** `MCPClientManager.call_tool(server, tool, arguments, ...)` menolak tool di luar `allowed_tools` dengan `{"status":"blocked"}`; tool diekspos ke LLM sebagai `f"{server}_{tool}"` dan dipetakan balik; `max_calls_per_minute > 0` ditegakkan dengan sliding window per server.

- [ ] **Step 1: Test gagal** — tool tidak diizinkan → blocked + audit `mcp_tool_denied`; kuota 2 → panggilan ketiga dalam 60 detik blocked; pemetaan nama bolak-balik benar.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** — jangan longgarkan default `allowed_tools: ["*"]` menjadi tanpa pengecekan; tetap hormati nilai config.
- [ ] **Step 4: Jalankan, pastikan lulus** + `pytest tests -q -k mcp`.
- [ ] **Step 5: Commit** — `feat: policy MCP namespacing + kuota per server`.

### Task 10: Proxy terstruktur (adapter lokal, Caido opsional)

**Files:**
- Create: `wth_ai/proxy/__init__.py`, `wth_ai/proxy/local.py`, `wth_ai/proxy/caido.py`
- Modify: `wth_ai/recon/http.py` (hook proxy), `wth_ai/netguard.py` (proxy tidak memperluas allowlist)
- Test: `tests/test_proxy_local.py`

**Interfaces:** `class ProxyAdapter(Protocol)` dengan `history(limit: int = 20) -> list[dict[str, Any]]` dan `replay(entry_id: str) -> dict[str, Any]`; `LocalProxyAdapter` membaca jurnal request/response yang sudah ditulis `recon/http.py`; `CaidoAdapter` aktif hanya bila `WTH_CAIDO_URL` diset dan host-nya lolos allowlist; tanpa Caido → `ProxyUnavailable`.

- [ ] **Step 1: Test gagal** — `LocalProxyAdapter.history` mengembalikan entri dengan `request`/`response`/`tx_id`; `replay` memakai `EgressGuard` dan menolak host di luar scope; `CaidoAdapter` tanpa env → `ProxyUnavailable`.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** — tidak ada dependency `caido-sdk-client`; pakai HTTP biasa ke API Caido bila ada.
- [ ] **Step 4: Jalankan, pastikan lulus.**
- [ ] **Step 5: Commit** — `feat: proxy terstruktur lokal + adapter caido opsional`.

---

## Phase 4 — Agen graph

### Task 11: Registry agen, spawn/send/wait dengan batas

**Files:**
- Create: `wth_ai/agents/graph.py`, `wth_ai/agents/graph_store.py`
- Modify: `wth_ai/agents/subagent/delegate.py:13`, `wth_ai/agents/subagent/registry.py` (`MAX_DEPTH`), `wth_ai/agents/supervisor_dispatch.py`
- Test: `tests/test_agent_graph.py`

**Interfaces:** `AgentGraph.spawn(agent_id, prompt, *, parent_id, depth) -> str` (mengembalikan `node_id`), `AgentGraph.send(node_id, message) -> None`, `AgentGraph.wait(node_id, timeout_s) -> dict[str, Any]`, `AgentGraph.tree() -> dict[str, Any]`. Batas: `MAX_NODES = 8`, `MAX_DEPTH` yang sudah ada, satu run paralel per node. Setiap node mewarisi `scope`, `mode`, `budget`; kill switch menghentikan semua node.

- [ ] **Step 1: Test gagal** — spawn melebihi `MAX_NODES` → penolakan; `wait` timeout → status `timeout`; node anak memakai scope parent (host di luar scope → `EgressDenied`); `KillSwitch.activate` → semua node `killed`.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** — state di `wth_home()/runtime/agents/<run>/graph.json` dengan `atomic_write_text`; tidak ada thread daemon tanpa join.
- [ ] **Step 4: Jalankan, pastikan lulus.**
- [ ] **Step 5: Commit** — `feat: agen graph dengan batas node dan pewarisan scope`.

### Task 12: Panel agen di TUI (read-only)

**Files:**
- Modify: `wth_ai/cli/tui/app.py`, `wth_ai/cli/tui/theme.py`
- Test: `tests/test_tui_agent_panel.py`

**Interfaces:** Panel membaca `AgentGraph.tree()` dan menampilkan `node_id | agent | status | durasi`; tidak ada tombol yang memutasi graf.

- [ ] **Step 1: Test gagal** — renderer menerima tree fixture dan menghasilkan teks berisi 3 node.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** — tanpa Rich Table baru bila sudah ada pola.
- [ ] **Step 4: Jalankan, pastikan lulus.**
- [ ] **Step 5: Commit** — `feat: panel agen read-only di TUI`.

---

## Phase 5 — Benchmark lokal

### Task 13: Harness benchmark offline

**Files:**
- Create: `wth_ai/bench/__init__.py`, `wth_ai/bench/harness.py`, `wth_ai/bench/tasks/` (3 task: redis-like IDOR, JWT none-alg, Modbus holding read)
- Modify: `wth_ai/cli/wth-ai-main.py` (`bench` command)
- Test: `tests/test_bench_harness.py`

**Interfaces:** `run_task(task_dir: Path) -> BenchResult(task, passed, findings, duration_s, ground_truth_hits)`, `score(results) -> dict[str, float]` (precision/recall terhadap `ground_truth.json`). Semua task memakai server palsu lokal; tanpa internet.

- [ ] **Step 1: Test gagal** — task fixture dengan satu temuan benar → recall 1.0; temuan salah → precision < 1.0; task tanpa `ground_truth.json` → error jelas.
- [ ] **Step 2: Jalankan, pastikan gagal.**
- [ ] **Step 3: Implementasi** — `wth-ai bench --tasks DIR --out DIR`, keluar 0/1.
- [ ] **Step 4: Jalankan, pastikan lulus** + `pytest tests -q -k bench`.
- [ ] **Step 5: Commit** — `feat: harness benchmark offline dengan skor precision/recall`.

---

## Phase 6 — Izin berbatas (default mati, terpisah dari fitur di atas)

Setiap task di phase ini punya flag eksplisit, default mati, dan **tidak** dieksekusi sebelum Phase 0–5 hijau.

### Task 14: `wth-ai view` (dashboard read-only)
- Files: `wth_ai/view/server.py` (baru), `wth_ai/cli/wth-ai-main.py`
- Interfaces: `serve(bundle_dir: Path, port: int = 0, token: str) -> None` — bind `127.0.0.1` saja, rute GET: `/`, `/findings`, `/manifest`; setiap respons 403 tanpa token.
- Test: `tests/test_view_server.py` — token salah → 403; bind address `127.0.0.1`; tidak ada rute POST.
- Commit: `feat(view): dashboard read-only loopback`.

### Task 15: Auto-fix PR berbatas
- Files: `wth_ai/autofix/planner.py`, `wth_ai/autofix/apply.py`, `wth_ai/cli/wth-ai-main.py`
- Interfaces: `plan_fix(finding) -> Patch`, `apply_patch(patch, worktree: Path) -> ApplyResult`; hanya `mode == "full_lab"` dan `--destructive`; `git push` tidak pernah dipanggil (test regresi memastikan tidak ada `"push"` di argv).
- Test: `tests/test_autofix_gate.py`
- Commit: `feat(autofix): patch + retest di worktree terpisah, tanpa push`.

### Task 16: Updater bertanda tangan
- Files: `wth_ai/update/verifier.py`
- Interfaces: `verify_artifact(path, signature_path, pubkey_path) -> bool`; host allowlist lewat `EgressGuard`.
- Test: `tests/test_update_verify.py` (fixture kunci + signature; signature rusak → False)
- Commit: `feat(update): verifikasi tanda tangan sebelum pasang`.

### Task 17: Telemetry opt-in agregat
- Files: `wth_ai/telemetry/aggregate.py`
- Interfaces: `build_report(run_meta) -> dict[str, Any]` hanya berisi `{runs, durations, exit_codes, version}`; test memastikan tidak ada host/temuan/secret.
- Commit: `feat(telemetry): agregat opt-in tanpa data target`.

### Task 18: Worker lokal (bukan distributed remote)
- Files: `wth_ai/jobs/local_queue.py`
- Interfaces: antrean berbasis file + `fcntl.flock`, satu worker per job, tanpa network listener.
- Commit: `feat(jobs): antrean worker lokal berbasis file`.

---

## Waves (untuk eksekusi)

| Wave | Tasks | Prasyarat |
|---|---|---|
| 1 | 1 | review pemilik repo (perubahan konstitusi) |
| 2 | 2, 3, 4 | — |
| 3 | 5, 6, 7 | — |
| 4 | 8, 9, 10 | — |
| 5 | 11, 12 | 2, 3 (anggaran tool-call dipakai node) |
| 6 | 13 | 5–7 (benchmark menilai SAST) |
| 7 | 14–18 | Phase 0 disetujui; dijalankan hanya bila diminta |

## Risiko dan rollback

- **Compaction membuang bukti** → test eksplisit `[TX:`; bila gagal, matikan compaction via config.
- **SAST memberi rasa aman palsu** → temuan statis terkunci di `candidate`; jangan tampilkan di laporan sebagai "vulnerability" tanpa label `origin: static`.
- **Agen graph memperbesar blast radius** → batas node + pewarisan scope + kill switch; matikan fitur dengan `config.agents_graph.enabled=false` (default `false` sampai Phase 5 hijau).
- **Amandemen AGENTS.md disalahpahami sebagai izin umum** → setiap izin menyebut flag dan default; tidak ada fitur Phase 6 yang menyala otomatis.
- Rollback: setiap phase satu rangkaian commit; `git revert` per commit tanpa menyentuh phase lain.
