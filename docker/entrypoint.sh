#!/usr/bin/env bash
set -euo pipefail

export WTH_HOME="${WTH_HOME:-/data/wth-ai}"
mkdir -p "$WTH_HOME"/{config,data,logs,runtime,wth-results,tools/bin}

cd /app

# Migrate env secrets into vault on first boot
if [[ -f /app/.env ]] || [[ -n "${ROUTER9_API_KEY:-}" ]] || [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  python3 -c "
from wth_ai.workspace import init_workspace
from wth_ai.vault import SecretVault, migrate_secrets
from wth_ai.config import USER_CONFIG_PATH, USER_ENV_PATH
init_workspace()
migrate_secrets(USER_CONFIG_PATH, USER_ENV_PATH, SecretVault())
" 2>/dev/null || true
fi

# Store env API keys in vault
python3 -c "
import os
from wth_ai.vault import SecretVault
v = SecretVault()
mapping = {
    'ROUTER9_API_KEY': 'router9_api_key',
    '9ROUTER_API_KEY': 'router9_api_key',
    'OPENROUTER_API_KEY': 'openrouter_api_key',
    'ANTHROPIC_API_KEY': 'anthropic_api_key',
    'OPENAI_API_KEY': 'openai_api_key',
    'GOOGLE_API_KEY': 'google_api_key',
    'GROQ_API_KEY': 'groq_api_key',
}
for env, key in mapping.items():
    val = os.environ.get(env)
    if val:
        v.set(key, val)
" 2>/dev/null || true

exec python3 /app/wth-main.py "$@"
