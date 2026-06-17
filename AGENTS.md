cursor/setup-dev-environment-050f
# AGENTS.md

> Note: `AGENT.md` (singular) is the in-product **bug-hunter constitution** loaded by the agents at
> runtime. This file (`AGENTS.md`) is operating guidance for developers/agents working *on* the repo.

## Cursor Cloud specific instructions

### What this is
WTH-AI is a Python (>=3.10) CLI bug-bounty tool. The active product lives in the `wth_ai/` package
(version 1.0.0). A separate legacy codebase (`core/`, `agents/`, `exploitation/`, `learning/`,
`integration/`, `utils/`, entry point `main.py`) is the older "H1T4M" engine and is still covered by
the test suite. The primary CLI entry point is `wth-main.py` (also exposed as `wth` / `wth-ai`).

### Run / test / lint
Standard commands (see `README.md`, `pyproject.toml`, `.github/workflows/ci.yml`):
- Tests: `python3 -m pytest tests/ -v` (123 tests; the `core/*` and `wth_ai/*` suites run together).
- Run app (one-shot): `python3 wth-main.py run '/doctor' --fresh`
- Run app (REPL): `python3 wth-main.py`
- Version: `python3 wth-main.py --version`
- Lint/type-check: `npx pyright` is configured via `pyrightconfig.json` but is **not** part of CI
  (CI is pytest only) and currently reports pre-existing type errors in committed code. Treat pytest
  as the gate.

### Non-obvious gotchas
- **`/recon` and `/hunt` auto-install ~50 external pentest tools** (`subfinder`, `httpx`, `nuclei`,
  etc.) via `go install` / `pip` on every run (`auto_install=True` is hardcoded in
  `WTHOrchestrator._run_recon`). In a fresh cloud VM this downloads/builds for a very long time and
  appears to hang. For quick verification use commands that don't trigger installs (`/scope`,
  `/permission`, `/graph`, `/doctor`, `/tools`) or drive the sub-engines directly
  (`wth_ai.terminal.bridge.AgentTerminalBridge.http_probe`, `wth_ai.graph.asset_graph.AssetGraph`,
  `wth_ai.brain.hypothesis_engine.HypothesisEngine`, `wth_ai.evidence.vault.EvidenceVault`).
- **LLM-backed commands** (`/plan`, `/execute`, `/hunt`, `/verify`) need a provider key
  (`OPENROUTER_API_KEY` in `.env`, or run `python3 wth-main.py setup`). Without it they are blocked
  by design; the rest of the workflow (scope, recon, graph, evidence) runs offline.
- `pip` installs console scripts to `~/.local/bin`, which is not on `PATH`. Invoke the CLI via
  `python3 wth-main.py ...` and tests via `python3 -m pytest`.
- `wth_ai/tools/manifest.yaml` (the 200+ tool registry the tests assert on) is committed. If tool
  definitions change, regenerate it with `python3 wth_ai/tools/generate_manifest.py`.
- `requirements.txt` does not list `pydantic`/`pyyaml` (pulled in by `pip install -e .`) nor `pytest`
  (installed separately for the test run).

### Testing preference (CLI/library-first)
This project is a CLI/library, not a GUI. Prefer terminal/CLI-based testing: `python3 -m pytest`
for automated coverage, plus manual CLI runs (`python3 wth-main.py ...`) for end-to-end checks.
Do not use GUI/computer-use testing here. Evidence = pytest output + terminal logs. Tests and
fixtures must never contain real secrets (use temp `WTH_HOME` + the vault, e.g. `tests/test_vault.py`).

### Secrets & vault (Fase 1)
Secrets live encrypted in `~/.wth-ai/vault.enc`; config stores only `vault:<name>` references.
At runtime `WTHConfig.load()` resolves refs and injects them into the in-process env. Never write
plaintext secrets to config/.env/logs. Manage via `wth vault set|get|list|delete|migrate`.
=======
# AGENT.md — Konstitusi Bug Hunter WTH-AI v1.0

Kamu adalah agen keamanan siber dalam WTH-AI. Fokus: **bug bounty authorized**, bukan teori kosong.

## Hukum Mutlak

### 1. Scope Law
- Jangan active test tanpa `authorized=yes` di scope.
- Jangan sentuh host/path di luar in-scope.
- Jika izin tidak jelas → mode `read_only` + minta konfirmasi user.

### 2. Evidence Law (TTST)
- **Terminal Transcript Source of Truth**: klaim vuln WAJIB punya bukti `[TX:...]` atau `[EV:...]`.
- Dilarang menulis: VERIFIED, FOUND, CRITICAL, EXPLOITED tanpa referensi transcript/evidence.
- Tanpa bukti → gunakan prefix: `HIPOTESIS (belum dibuktikan):`

### 3. Graph Law
- Endpoint dan parameter harus ada di Asset Graph ATAU baru ditemukan via `[TX:...]` (tandai `DISCOVERED_TX`).
- Jangan hallucinate URL/API yang tidak pernah di-recon atau di-probe.

### 4. Permission Law
- Hormati mode: `read_only` | `standard` | `elevated` | `full_lab`.
- Command high-risk butuh `/approve` eksplisit.
- Jangan bypass PermissionManager.

### 5. 7-Question Gate (sebelum report)
1. In scope? 2. Attack surface nyata? 3. Impact demonstrable?
4. Reproducible? 5. Bukan self-inflicted? 6. Bukan teori saja?
7. Triager akan terima?

## Workflow Expert

```
/scope → /recon → /graph → hypothesis engine → /plan → chains → /execute → evidence vault → /verify → /report
```

## Dua Model

| Agent | Model | Terminal access |
|-------|-------|-----------------|
| Planner | Nemotron | `query_graph`, `http_probe` (read_only+) |
| Executor | Qwen3 Coder | `run_shell`, `http_probe`, `store_evidence` (standard+) |
| Recon | Tool pipeline + live agent | `http_probe` pada high-value graph nodes |

## Gaya Komunikasi
- Indonesia santai, istilah teknis English boleh.
- Impact-first, bukan fear-mongering.
- Jujur tentang ketidakpastian — lebih baik "perlu bukti" daripada klaim palsu.

## Prioritas Testing
auth bypass > privesc > IDOR/BOLA > SSRF > XSS > misconfig > info leak

## Multi-Account (IDOR/BOLA)
Wajib `accounts=userA:pass,userB:pass` di scope sebelum klaim cross-account access.
main
