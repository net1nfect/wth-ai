#!/usr/bin/env bash
# WTH-AI v1.0 installer — Kali PEP668 safe, no venv required
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

WTH_HOME="${WTH_HOME:-$HOME/.wth-ai}"
mkdir -p "$WTH_HOME"/{runtime,tools/bin,data/sessions,data/engagements,logs,config}

echo "[*] WTH-AI v1.0 installer"
echo "[*] Root: $ROOT"
echo "[*] WTH_HOME: $WTH_HOME"

PIP_FLAGS=(--user)
if python3 -m pip install --help 2>/dev/null | grep -q break-system-packages; then
  PIP_FLAGS+=(--break-system-packages)
  echo "[*] PEP 668 detected — using --break-system-packages --user"
fi

if python3 -m pip install "${PIP_FLAGS[@]}" -q -r requirements.txt \
  && python3 -m pip install "${PIP_FLAGS[@]}" -q -e .; then
  echo "[+] Installed: pip install ${PIP_FLAGS[*]} -e ."
else
  echo "[!] pip install failed — use source path (no install needed):"
  echo "    python3 wth-main.py run '/doctor'"
fi

python3 -m pip install "${PIP_FLAGS[@]}" -q nodriver 2>/dev/null \
  || echo "[!] nodriver optional — browser/CF bypass disabled until installed"

mkdir -p data/sessions data/cli_history wth-results skills

if [[ -d "$HOME/.openclaw/workspace/skills" ]]; then
  ln -sfn "$HOME/.openclaw/workspace/skills" "$ROOT/skills/external" 2>/dev/null || true
  echo "[+] Linked OpenClaw skills"
fi

if [[ -d "$HOME/.codex/skills" ]]; then
  ln -sfn "$HOME/.codex/skills" "$ROOT/skills/codex" 2>/dev/null || true
  echo "[+] Linked Codex skills"
fi

if [[ ! -f .env ]] && [[ -f .env.example ]]; then
  cp .env.example .env
  echo "[!] Created .env — set OPENROUTER_API_KEY"
fi

if [[ ! -f "$WTH_HOME/.env" ]] && [[ -f .env ]]; then
  cp .env "$WTH_HOME/.env" 2>/dev/null || true
fi

# Primary launcher — always uses wth-main.py (avoids broken entry imports)
LAUNCHER="$HOME/.local/bin/wth-ai"
mkdir -p "$HOME/.local/bin"
cat > "$LAUNCHER" << EOF
#!/usr/bin/env bash
export WTH_HOME="\${WTH_HOME:-$WTH_HOME}"
export PATH="\$WTH_HOME/tools/bin:\$HOME/.local/bin:\$PATH"
exec python3 "$ROOT/wth-main.py" "\$@"
EOF
chmod +x "$LAUNCHER"
echo "[+] Launcher: $LAUNCHER"

# npm local link (registry not published yet)
if command -v npm >/dev/null 2>&1; then
  if (cd "$ROOT/packages/wth-cli" && npm link 2>/dev/null); then
    echo "[+] npm linked: wth → packages/wth-cli (local)"
  else
    echo "[*] npm link skipped — run: npm i -g $ROOT/packages/wth-cli"
  fi
fi

echo ""
echo "=== Run paths ==="
echo "  1) wth-ai run '/doctor'          (launcher → wth-main.py)"
echo "  2) python3 wth-main.py           (source dev)"
echo "  3) npm i -g ./packages/wth-cli   (local npm, until registry publish)"
echo "  4) wth-ai setup                    (first-run wizard)"
echo ""
python3 wth-main.py --version 2>/dev/null || true
if [[ ! -f "$WTH_HOME/config/user.yaml" ]]; then
  echo "[*] First install — jalankan: wth-ai setup"
fi
