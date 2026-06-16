# AGENTS.md

Repository operating notes for AI agents. For the bug-hunter behavioral constitution
(scope law, TTST evidence rules, permission modes), see [`AGENT.md`](AGENT.md).

## Cursor Cloud specific instructions

### Repository layout (two products)
- **WTH-AI v1.0 (primary, actively maintained):** the `wth_ai/` package. Run it via
  `python3 wth-main.py` (interactive REPL) or `python3 wth-main.py run "<command>"`
  (one-shot). The installed console scripts `wth` / `wth-ai` (`wth_ai.cli.entry:main`)
  simply re-exec `wth-main.py`. Tests under `tests/test_wth_ai.py` cover this product.
- **Legacy H1T4M-AI (secondary):** `main.py` + `core/` + the top-level `agents/`,
  `exploitation/`, `learning/`, etc. Covered by `tests/test_agents.py`,
  `test_exploitation.py`, `test_integration.py`, `test_learning.py`.

### Running / building / testing
- Run app: `python3 wth-main.py --version`, `python3 wth-main.py run "/doctor"`,
  or `python3 wth-main.py` for the REPL. There is no separate build step (this is a
  Python CLI; release binaries are built with PyInstaller only in CI).
- Tests (matches CI in `.github/workflows/ci.yml`): `python3 -m pytest tests/` (52 tests).
- Tool manifest: `wth_ai/tools/manifest.yaml` is committed and already lists 200+ tools
  (the `test_tool_registry_loads_200_plus` test depends on this). CI regenerates it with
  `python3 wth_ai/tools/generate_manifest.py`; only re-run that if you change the tool
  catalog in `generate_manifest.py`.
- Lint: `pyright` (config in `pyrightconfig.json`). It currently reports some
  pre-existing type errors in `wth_ai/models.py` and `wth_ai/orchestrator.py`; lint is
  **not** gated in CI, so treat those as pre-existing unless you touched that code.

### LLM / secrets
- The dual-agent brain needs `OPENROUTER_API_KEY` (provided as an environment secret).
  When set, `run "/doctor"` shows `OpenRouter: ONLINE` and chat/plan use a live model
  (default `nvidia/nemotron-...:free` via OpenRouter). Without it, LLM-dependent flows
  fall back to offline/hypothesis-only behavior.
- `WTH_HOME` defaults to `~/.wth-ai` (config, sessions, tool bin live there).
  `WTH_SKIP_SETUP=1` bypasses the first-run setup wizard; `WTH_TEST_MODE=1` is set by tests.

### Gotchas
- `/recon` (and `/hunt`) run **real, multi-phase network recon** including an
  LLM-driven live-agent phase. Against real domains this can run for many minutes and
  may look hung in a sandbox — it is not a quick smoke test. For fast verification use
  `run "/doctor"`, a plain chat message, or `/scope set ...`. The terminal/HTTP evidence
  engine (TTST) can be exercised directly and quickly via
  `wth_ai.terminal.bridge.AgentTerminalBridge.http_probe(...)`.
- Running the CLI writes runtime artifacts into tracked paths (`data/engagements/...`,
  `logs/engagement_logs/...`) and creates untracked output (`reports/`,
  `data/ml_models/strategy_scores.json`). Do **not** commit these; `git checkout -- data/ logs/`
  to discard tracked-file churn before committing.
