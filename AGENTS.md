# AGENTS.md

> Note: `AGENT.md` (singular) in this repo is the WTH-AI bug-hunter *constitution* (agent behavior rules), not developer setup. This file is for engineering/dev-environment guidance.

## Cursor Cloud specific instructions

This repo contains two Python CLI products in one tree (no web UI, no Docker, file-backed SQLite):

- **WTH-AI v1.0** (primary) — entry point `wth-main.py` (also `wth-ai`/`wth` console scripts). Package: `wth_ai/`.
- **H1T4M-AI** (legacy companion) — entry point `main.py`. Packages: `core/`, `agents/`, `reconnaissance/`, `exploitation/`, etc.

### Environment

- Python deps are installed into the **user site** (`~/.local`) via `pip install --break-system-packages --user` (matches `install.sh`; PEP 668 applies, no venv). `~/.local/bin` is added to PATH in `~/.bashrc`.
- `pytest` and `pyright` are dev-only tools not declared in repo metadata; the startup update script installs them.
- The committed `wth_ai/tools/manifest.yaml` (215 tools) is required by tests. It is regenerated deterministically by `python3 wth_ai/tools/generate_manifest.py`; do not commit incidental regenerations.

### Run

- WTH-AI: `python3 wth-main.py --version`, `python3 wth-main.py run '/doctor'`, `python3 wth-main.py interactive`. Offline flows work because `config/wth_config.yaml` has `require_llm: false`; LLM-backed `/plan`, `/execute`, `/hunt`, `/verify`, `/report` need `OPENROUTER_API_KEY` (or another provider). `--skip-setup` / `WTH_SKIP_SETUP=1` bypasses the first-run wizard.
- H1T4M-AI: `python3 main.py`. It is authorization-gated — it raises `AuthorizationError` unless an authorization proof is supplied. There is no CLI flag; set `H1T4M_AUTHORIZATION_PROOF=...` (env). Reports are written to `reports/<engagement-id>.{json,html}`.

### Test / lint

- Tests: `python3 -m pytest tests/` (the test files set `WTH_TEST_MODE=1` and `WTH_SKIP_SETUP=1` themselves). All suites are offline (network/LLM/security tools are mocked or optional). CI also runs `python3 wth_ai/tools/generate_manifest.py` first (see `.github/workflows/ci.yml`).
- Type check: `pyright` (config in `pyrightconfig.json`). It currently reports pre-existing type errors and is **not** gated by CI — treat existing errors as baseline, not environment breakage.

### Cognition layer (`wth_ai/cognition/`)

A deterministic, offline reasoning brain that ranks what to test first across recon → planner → executor. It never executes commands or changes permissions — guardrails stay upstream (`PermissionManager`, `ScopeManager`, authorization, dry-run, TTST).

- `signals.py` — grounded feature extraction (vuln base severity per AGENT.md priority, param sensitivity, endpoint exposure, tech-profile affinity).
- `risk_model.py` — explainable weighted risk score (0–100) + severity band + confidence, lifted by cross-session learning priors (`HuntMemoryLearning.success_rate`).
- `reasoning_engine.py` — dedup → kill-list → score → rank → plan-confidence.
- `strategy_selector.py` — UCB1 bandit over execution strategies (adapts via `record_outcome`, seeded by learning).
- `engine.py` — `CognitiveEngine` facade used by the orchestrator.
- Exposed via the `/reason` slash command (`/reason` ranked plan, `/reason --targets` recon focus); also auto-ranks hypotheses inside `/plan`. All offline-testable (`tests/test_cognition.py`).

### Test planning layer (`wth_ai/planning/`)

Bridges cognition (ranked hypotheses) to the executor with a module-bound, gated, budgeted, non-destructive test queue (executor flowchart modules 3–12). Planning only — never sends requests or escalates permissions.

- `modules.py` — vulnerability module taxonomy (authentication, access_control, injection, client_side, csrf_cors, file_upload, api, business_logic, configuration, cloud); each with non-destructive test families, min permission, evidence schema, preconditions.
- `test_planner.py` — `TestPlanner.build_plan(report, scope, permission_mode, request_budget)` → `TestPlan`: orders by cognition rank, gates by permission level + scope preconditions (IDOR needs 2 accounts; auth/upload need a test account) + request budget; emits `ExecutorTask`s.
- Exposed via `/testplan` (`/testplan --budget=N`); it also fills `state.task_queue` so `/execute` can pick up planned tasks. Offline-testable (`tests/test_planning.py`).

### Gotchas

- `AgentTerminalBridge.http_probe` enforces permission before scope: a `planner` actor needs `read_only`, an `executor` actor needs `standard` or higher. With an insufficient mode the probe returns `blocked=True`/`exit_code=127` (`status_code=0`) instead of performing the request — this is expected, not a network failure.
- `/recon` auto-installs missing security tools (go/apt/pip), up to 10 per phase, so it is slow and network-heavy on a fresh box (only ~15/215 tools preinstalled). Avoid it for quick smoke checks.
- Running the apps/tests mutates tracked runtime files (`data/cli_history`, `data/engagements/**`, `logs/**`) and creates `reports/`, `data/ml_models/`. These are runtime byproducts — do not commit them.
