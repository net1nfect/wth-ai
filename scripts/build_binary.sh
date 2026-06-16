#!/usr/bin/env bash
# Build WTH-AI standalone binary with PyInstaller
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="${WTH_VERSION:-1.0.0}"
DIST="$ROOT/dist"
PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
[[ "$(uname -m)" == "x86_64" ]] && PLATFORM="${PLATFORM/x86_64/amd64}"
[[ "$(uname -m)" == "aarch64" ]] && PLATFORM="${PLATFORM/aarch64/arm64}"
[[ "$(uname -s)" == "Darwin" ]] && PLATFORM="darwin-${PLATFORM#darwin-}"

echo "[*] Building WTH-AI v${VERSION} for ${PLATFORM}"

if [[ -d venv ]]; then
  source venv/bin/activate
fi
# Build uses local venv only for CI/build hosts — end users never need venv

python3 -m pip install -q pyinstaller -r requirements.txt
python3 -m pip install -q -e .

mkdir -p "$DIST"

pyinstaller --noconfirm --clean \
  --name "wth-ai" \
  --onefile \
  --add-data "prompts:prompts" \
  --add-data "config:config" \
  --add-data "wth_ai/tools/manifest.yaml:wth_ai/tools" \
  --add-data "AGENT.md:." \
  --hidden-import=wth_ai \
  --hidden-import=wth_ai.cli.entry \
  --hidden-import=wth_ai.orchestrator \
  --hidden-import=discord \
  --hidden-import=telegram \
  --collect-submodules=wth_ai \
  --collect-submodules=click \
  "$ROOT/wth_ai/cli/entry.py"

OUT="$DIST/wth-ai-${PLATFORM}"
mv "$DIST/wth-ai" "$OUT"
chmod +x "$OUT"

echo "[+] Binary: $OUT"
sha256sum "$OUT" > "${OUT}.sha256"
echo "[+] Checksum: ${OUT}.sha256"
