# WTH-AI v1.0 — Realtime Bug Hunter

WTH-AI adalah CLI bug bounty expert yang menggabungkan **multi-provider LLM**, **terminal akses terkontrol permission**, **asset graph**, **Discord/Telegram bot**, dan **anti-halusinasi berbasis bukti (TTST)**.

> Hanya untuk **authorized testing**: milik sendiri, lab, CTF, atau bug bounty in-scope.
> **v1.0** — first public release. Semua eksekusi real-time, tanpa simulasi.

---

## Apa yang Membuat WTH-AI Berbeda

| Fitur | Scanner biasa | WTH-AI v1.0 |
|-------|---------------|-------------|
| Otak | Prompt panjang | Graph + rule engine + LLM refine |
| Eksekusi | Satu tool | Planner/Executor **langsung curl & shell** via Agent Terminal |
| Klaim vuln | Sering halusinasi | **TTST**: wajib `[TX:...]` transcript bukti |
| Setup | Manual config | **Setup wizard** pertama kali (seperti OpenClaw) |
| Model | Satu provider | OpenRouter + Ollama + OpenAI/Anthropic/Google |
| Bot | Tidak ada | Discord & Telegram daemon |

---

## Arsitektur

```mermaid
flowchart TD
    A([START]) --> B["0. Start / Intake"]

    B --> B1["Target request"]
    B --> B2["Scope definition"]
    B --> B3["Rules of engagement"]
    B --> B4["Credential / test account if provided"]
    B --> B5["Authorization check"]

    B --> C["1. Scope & Policy Engine"]

    C --> C1["Allowlist / denylist"]
    C --> C2["Rate limits"]
    C --> C3["Safe mode"]
    C --> C4["Environment tagging: prod / staging / dev"]
    C --> C5["Approval gates"]

    C --> D["2. Recon & Attack Surface Mapping"]

    D --> D1["Root domain expansion"]
    D --> D2["Passive collection"]
    D --> D3["DNS / IP / ASN mapping"]
    D --> D4["HTTP probing"]
    D --> D5["Fingerprinting"]
    D --> D6["Crawling / content discovery"]
    D --> D7["JS / parameter / API mapping"]
    D --> D8["Cloud / auth / header / TLS mapping"]

    D --> E["3. Normalization & Asset Graph"]

    E --> E1["Deduplication"]
    E --> E2["Confidence scoring"]
    E --> E3["Entity linking"]
    E --> E4["Domain -> Subdomain"]
    E --> E5["Subdomain -> IP"]
    E --> E6["IP -> URL"]
    E --> E7["URL -> Endpoint"]
    E --> E8["Endpoint -> Parameter"]
    E --> E9["Screenshot / evidence linkage"]

    E --> F["4. Prioritization Engine"]

    F --> F1["Interest / risk scoring"]
    F --> F2["Admin / dev keyword scoring"]
    F --> F3["Auth surface weight"]
    F --> F4["Secret / cloud exposure weight"]
    F --> F5["Business-critical asset scoring"]
    F --> F6["Select top targets"]

    F --> G["5. Test Planning Layer"]

    G --> G1["Choose modules"]
    G --> G2["Create test queue"]
    G --> G3["Sequence authenticated vs unauthenticated flows"]
    G --> G4["Plan request budget"]
    G --> G5["Define stop conditions"]

    G --> H["6. Executor Engine"]

    H --> H1["Request workers"]
    H --> H2["Session manager"]
    H --> H3["Payload / test logic library"]
    H --> H4["Auth / access control validation"]
    H --> H5["Injection validation"]
    H --> H6["API / GraphQL / upload modules"]
    H --> H7["Business logic modules"]
    H --> H8["Safety guardrails"]

    H --> I["7. Response Analysis & Evidence"]

    I --> I1["Compare baseline vs mutated responses"]
    I --> I2["Detect anomalies"]
    I --> I3["Capture request / response"]
    I --> I4["Capture screenshots"]
    I --> I5["Create reproduction notes"]
    I --> I6["Confidence scoring"]

    I --> J["8. Correlation, Triage, Human Review"]

    J --> J1["Deduplicate findings"]
    J --> J2["Cluster related issues"]
    J --> J3["False-positive filtering"]
    J --> J4["Analyst review"]
    J --> J5["Reproduce high-confidence findings"]
    J --> J6["Decide severity candidate"]

    J --> K["9. Reporting & Deliverables"]

    K --> K1["Executive summary"]
    K --> K2["Technical findings"]
    K --> K3["Affected assets"]
    K --> K4["Evidence bundle"]
    K --> K5["Prioritized remediation areas"]
    K --> K6["Retest checklist"]

    K --> L["10. Remediation Loop & Continuous Operation"]

    L --> L1["Remediation tracking"]
    L --> L2["Retest queue"]
    L --> L3["Change detection"]
    L --> L4["New asset monitoring"]
    L --> L5["Continuous recon feedback"]
    L --> L6["Model / rule tuning"]

    L --> M["Output"]

    M --> M1["Attack Surface Inventory"]
    M --> M2["Prioritized Targets"]
    M --> M3["Validated Finding Candidates"]
    M --> M4["Evidence & Reports"]
    M --> M5["Retest Pipeline"]

    M5 --> D
    L5 --> D
    L6 --> F

    M --> END([END])

    SUPPORT["Komponen Pendukung"] --> SUP1["Central Data Store"]
    SUPPORT --> SUP2["Secrets-Safe Vault"]
    SUPPORT --> SUP3["Screenshot Storage"]
    SUPPORT --> SUP4["Logs / Audit Trail"]
    SUPPORT --> SUP5["Notification & Alerts"]
    SUPPORT --> SUP6["Dashboard / Metrics"]
    SUPPORT --> SUP7["Worker Scaling / Orchestration"]

    SUP1 -.-> E
    SUP2 -.-> H
    SUP3 -.-> I
    SUP4 -.-> I
    SUP5 -.-> K
    SUP6 -.-> F
    SUP7 -.-> H

    PRINCIPLES["Prinsip Kunci"] --> P1["Authorized only"]
    PRINCIPLES --> P2["Evidence-driven"]
    PRINCIPLES --> P3["Non-destructive validation"]
    PRINCIPLES --> P4["Rate-aware execution"]
    PRINCIPLES --> P5["Human-in-the-loop"]
    PRINCIPLES --> P6["Continuous improvement"]

    PRINCIPLES -.-> C
    PRINCIPLES -.-> H
    PRINCIPLES -.-> J
```

**TTST (Terminal Transcript Source of Truth):** Setiap klaim kerentanan harus punya output terminal/HTTP nyata yang tersimpan sebagai `TX-{id}`.

Konstitusi lengkap: lihat [`AGENT.md`](AGENT.md).

---

## Instalasi (tanpa source code)

### Publik — npm + binary

```bash
npm install -g @wth-ai/cli
wth                    # download binary + setup wizard otomatis
wth-ai setup           # re-run wizard
```

Binary disimpan di `~/.wth-ai/runtime/`. Source code tidak diperlukan.

### Developer — dari source

```bash
git clone <private-repo> wth-ai && cd wth-ai
pip install --break-system-packages --user -e .
./install.sh
wth-ai setup
```

### Setup Wizard (first-run)

Wizard meminta:
1. Provider LLM (OpenRouter / Ollama / OpenAI / Anthropic / Google)
2. API key (validasi ping live)
3. Model per role: planner, executor, verifier
4. Permission default
5. Opsional: Discord & Telegram bot tokens

Config tersimpan di `~/.wth-ai/config/user.yaml` dan `~/.wth-ai/.env`.

### Jalankan

```bash
wth-ai                  # REPL interaktif
wth-ai run "/doctor"    # one-shot command
wth-ai bot discord      # Discord daemon
wth-ai bot telegram     # Telegram daemon
```

---

## Permission Modes

| Mode | Command | Terminal access |
|------|---------|-----------------|
| `read_only` | `/permission read_only` | curl, httpx, HEAD/GET, static tools |
| `standard` | `/permission standard` | + nuclei, ffuf, arjun, **run_shell** testing |
| `elevated` | `/permission elevated` | + sqlmap, dalfox (butuh `/approve`) |
| `full_lab` | `/permission full_lab` | Lab/CTF only — tetap ada denylist |
| `unrestricted` | `/permission unrestricted` | Semua tool; scope tetap wajib |
| `godmode` | `/permission godmode` | **Tanpa batas**: semua tool, semua target, tanpa approval, tanpa rate/iterasi limit |

### No-limits (godmode)

Untuk lab/CTF/target milik sendiri di mana guardrail hanya menambah friksi:

```bash
wth safety godmode on      # setara: wth safety unrestricted on
wth-ai run "/permission godmode"
```

Yang dimatikan: scope/authorization check, approval, denylist, rate limit, batas
iterasi/cycle/depth subagent, redaksi secret, filter tool per-role, dan konstitusi
`AGENT.md`. Yang tetap ada: bukti `[TX:...]`, pemulihan payload destruktif, dan
laporan — supaya hasil tetap bisa ditulis.

`yolo` tetap berbeda: ia menaikkan capability dan mematikan approval, tetapi
**tetap mewajibkan authorization**.

---

## Malware Forge

WTH-AI bisa membangun artefak malware nyata (bukan sketsa): shellcode runner,
reflective PE loader, TCP implant, HTTP stager, dropper, dan beacon — native C,
PowerShell, Python, atau Bash. Setiap artefak ditulis ke `~/.wth-ai/malware/<id>/`
bersama source, README, `metadata.json`, hash, dan analisis statis (entropi, PE
section/import, YARA, indikator C2/anti-analysis). Build otomatis bila toolchain ada.

```bash
/malware help
/malware forge implant --c2 127.0.0.1:4444 --features amsi,etw,antidebug,sleepmask
/malware forge loader --os windows --arch x64
/malware analyze ~/.wth-ai/malware/<id>/implant.exe
/malware encode stage.bin --format powershell
```

Agent memakai ini lewat tool `malware_forge` / `malware_analyze` / `malware_encode`
dan skill `malware-forge`. `shellcode`/`dropper` menghasilkan payload **msfvenom asli**
(`--payload linux/x64/exec --opt CMD=...`); tanpa msfvenom tool menolak, bukan menulis placeholder.

## Strix-Mode: Agent Menyerang Langsung (tanpa slash)

Doktrin agent dibesarkan dari arsitektur Strix (usestrix/strix):
AI yang **menyerang, bukan menjawab** — ia memutuskan langkah berikutnya dari
respons terakhir target, bukan dari checklist statis:

| Strix capability | WTH-AI feature |
|------------------|----------------|
| Reason from the last response | `http_probe` sudah adaptif otomatis: 403/406/429/401/WAF memicu katalog mutasi (header/encoding/method) dan laporan `[ADAPTIF] ... BYPASS` — **tanpa `/adaptive`** |
| Full hacker toolkit | `run_shell`, `recon_run_tool`, `oob_*`, `computer_use`, `malware_forge` |
| Python Runtime (custom exploit) | `python_runtime` — tulis skrip python sendiri, jalan via run_shell nyata (permission/audit sama) |
| Multi-agent orchestration | `delegate_task` — spawn subagent spesialis (injection, authz, recon, ctf…) |
| Real exploit validation | every finding wajib bukti `[TX:...]` + `[DIFF vs TX]` |
| Agentic memory | `memory_write`/`memory_read` persist antar sesi |
| Self-improvement | `self_improve` — pelajaran tersimpan dan kembali ke context turn berikutnya |
| Malware on demand | "buat malware X" di chat → tool `malware_forge` dipanggil agent SEKARANG, tanpa bawa `/malware` |

Semua ini sudah di-inject ke prompt supervisor + no-limits override, jadi agent
TIDAK menunggu perintah slash — ia menyerang, menulis kode, mempelajari, dan
mengingat secara mandiri.

## Adaptive & Proactive

Dua lapisan otonomi yang berjalan nyata di atas jalur probe/audit yang sama:

```bash
# Adaptive: baseline -> klasifikasi WAF/rate/auth -> mutasi -> ukur -> memori target
wth adaptive https://target.example/admin --rounds 3 --moves 8
/adaptive https://target.example/admin --header "Authorization: Bearer ..."

# Proactive: daemon siklus autopilot+adaptive untuk banyak target
wth autonomy add target.example other.example
wth autonomy start --interval 300 --steps 6
wth autonomy status
wth autonomy stop
```

- `wth_ai/adaptive/` — signal classifier (Cloudflare/Akamai/Imperva/F5/AWS/…), 28 mutasi
  path/header/encoding/method, memori per target di `$WTH_HOME/adaptive/<host>.json`.
- `wth_ai/continuous/` — daemon `start/stop/status`, state/PID/log di `$WTH_HOME/runtime/`.
- Setiap respons 403/406/429/401/WAF di autopilot otomatis memicu retry adaptif dan
  mencatat strategi yang menang; langkah adaptif tidak memakan budget siklus utama.
- TUI `Ctrl+6` membuka **Ops** dashboard: baseline, strategy, attempt, bypass, daemon counters.
- `--safe` / `WTH_NO_LIMITS=0` mengembalikan scope+approval; default build ini no-limits.

## Quick Start (5 menit)

```bash
wth-ai
/permission standard
/scope set target=app.example.com authorized=yes accounts=alice@x.com:pass1,bob@x.com:pass2
/recon
# → target › app.example.com
# → authorized › yes
/graph
/plan
/execute --all
/verify
/report
```

---

## Workflow Lengkap — Contoh Real-Time

### 1. Set Scope + Multi-Account

```bash
/scope set target=app.target.com authorized=yes accounts=userA@test.com:Secret1,userB@test.com:Secret2
/scope
```

Output menampilkan target, authorization, dan test accounts.

### 2. Recon Interaktif (Wizard)

```bash
/recon
```

Tanpa argumen:
1. Prompt berubah ke `target ›`
2. Ketik domain: `app.target.com`
3. Jika belum authorized → `authorized › yes`
4. Full recon 12 fase + live agent http_probe
5. Asset graph terisi otomatis

Contoh output:
```
## Recon — app.target.com
- Graph nodes: 47
- Graph coverage: 0%
- Parameters: 23
- API endpoints: 8
```

### 3. Lihat Asset Graph

```bash
/graph
```

Menampilkan nodes, edges, top params (`id`, `user_id`, `redirect`, dll).

### 4. Plan — Hypothesis + Chains

```bash
/plan Audit IDOR and SSRF on API endpoints
```

Planner:
1. Rule engine generate pre-hypotheses dari graph
2. LLM refine (JSON structured)
3. **Probe phase:** planner `http_probe` top URLs → `[TX:...]` citations
4. Chain planner buat exploit steps

### 5. Execute — Testing Langsung

```bash
/execute --all
```

Executor (Qwen3 Coder) via **Agent Tool Loop**:
- `run_shell` → `httpx`, `curl`, `nuclei` (sesuai permission)
- `http_probe` → request/response langsung
- Output disimpan sebagai `[TX:abc123]`

Untuk IDOR task (`evidence_schema.type: idor`):
- Butuh 2 accounts di scope
- Baseline userA → fetch as userB → diff response

### 6. Verify — 7-Question Gate

```bash
/verify
```

Verifier + Grounding Firewall:
- Tolak klaim tanpa `[TX:...]` atau evidence vault
- Schema gate: proof type harus match

### 7. Report H1-Ready

```bash
/report
```

Report include PoC blocks:

```http
GET https://app.target.com/api/orders/123
```

**Response:** `200` + body snippet (redacted).

---

## Hunt Otomatis (Graph-Driven)

```bash
/hunt "Comprehensive vuln hunt on API and auth flows"
```

Loop: recon → graph → hypotheses → plan → execute → verify → replan (max 5 iterasi).

Status bar menampilkan: `hyp 12 · chains 3 · cov 15% · verified 1`

---

## Perintah Utama

| Command | Fungsi |
|---------|--------|
| `/recon` | Wizard domain atau recon langsung |
| `/graph` | Ringkasan asset graph |
| `/plan` | Hypotheses + exploit chains |
| `/execute [--all]` | Jalankan tasks via terminal bridge |
| `/hunt` | Full automated loop |
| `/verify` | Validasi bukti |
| `/report` | Laporan H1 dengan PoC |
| `/permission` | Set mode testing |
| `/scope set accounts=...` | Multi-account untuk IDOR |
| `/adaptive <url>` | Bypass adaptif: sinyal → mutasi → ukur → memori |
| `/autonomy start\|stop\|status\|add` | Daemon proaktif siklus autopilot+adaptive |

---

## Anti-Halusinasi (TTST)

1. **AGENT.md** — konstitusi wajib untuk semua agent
2. **GroundingFirewall** — klaim VERIFIED/FOUND tanpa `[TX:...]` diubah jadi `HIPOTESIS (belum dibuktikan)`
3. **Evidence Vault** — request/response pairs per finding
4. **Hypothesis Engine** — rules dari graph *sebelum* LLM mikir

---

## Troubleshooting

| Masalah | Solusi |
|---------|--------|
| LLM offline | Set `OPENROUTER_API_KEY` |
| Recon blocked | `/scope set authorized=yes` |
| IDOR blocked | Tambah `accounts=userA:pass,userB:pass` |
| Command blocked | `/permission standard` atau lebih tinggi |
| Tool missing | `/doctor` lalu `/tools install all` |
| pip PEP 668 | `pip install --break-system-packages --user -e .` |

---

## Keamanan & Etika

- Jangan test target tanpa izin tertulis (kecuali `godmode` — target dianggap authorized)
- Rate limit default: 2 req/s (ubah di scope)
- High-risk commands butuh `/approve` (kecuali `godmode`)
- Secrets di-redact otomatis di transcript & report (kecuali `godmode`)

---

## Struktur Proyek

```
wth_ai/
  terminal/     # AgentTerminalBridge + transcripts (TTST)
  graph/        # Asset graph
  brain/        # Hypothesis + chain planner
  agents/       # Planner, executor, tool loop, grounding
  executor/vuln/# IDOR, SSRF, XSS, auth, race
  adaptive/     # signal classifier + mutation engine + per-target memory
  continuous/   # proactive autonomy daemon + state/PID/log
  malware/      # forge implant/loader/dropper + analyzer
  recon/deep/   # JS, OpenAPI, GraphQL analysis
  evidence/     # Evidence vault
AGENT.md        # Konstitusi bug hunter
config/         # wth_config.yaml
```

---

## Versi

**v1.0.0** — First public release: realtime execution, setup wizard, multi-provider LLM, Discord/Telegram bots
