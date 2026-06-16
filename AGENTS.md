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
- Tests: `python3 -m pytest tests/ -v` (52 tests; the `core/*` and `wth_ai/*` suites run together).
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
