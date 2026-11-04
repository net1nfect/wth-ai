#!/usr/bin/env bash
# Installer WTH-AI — venv terisolasi, entry point `wth`, workspace ~/.wth-ai.
#
# Kenapa skrip dan bukan sekadar `pip install .`:
#   * `wth` perlu di PATH tanpa mengotori Python sistem.
#   * workspace (~/.wth-ai) punya permission khusus (vault 600, dir 700).
#   * install harus bisa diulang (upgrade) dan dibatalkan (--uninstall).
#   * boilerplate lama `AGENT.md` di symlink-repo sudah diganti `AGENTS.md`;
#     prompt bypass ada di `prompts/agent/AGENT.md` dan hanya dipakai bila
#     operator memintanya lewat `wth --bypass`.
#
# Tidak ada sudo, tidak ada shell=True, tidak ada unduhan di luar pip.
set -euo pipefail

VERSION="1.0.0"
WTH_HOME="${WTH_HOME:-$HOME/.wth-ai}"
BIN_DIR="${WTH_BIN_DIR:-$HOME/.local/bin}"

# Ada dua cara skrip ini dipanggil:
#   * dari checkout (`scripts/install.sh`) -> sumber = induk skrip ini;
#   * lewat pipa/`bash -s` (bootstrap satu baris) -> tidak ada file di disk,
#     jadi $0 bukan path yang bisa dipakai. Gunakan direktori kerja saat ini,
#     lalu `WTH_SRC`/`--repo` sebagai penentu akhir.
if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "-" && "${BASH_SOURCE[0]}" != "bash" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || printf '%s' "$PWD")"
else
  SCRIPT_DIR="$PWD"
fi
SRC_DIR="${WTH_SRC:-$SCRIPT_DIR/..}"
PYTHON="${WTH_PYTHON:-python3}"
FEATURES=""
DO_SETUP=1
DO_UNINSTALL=0
PURGE=0
DRY_RUN=0
YES=0
USE_VENV=1
#: Dipakai saat skrip dijalankan tanpa repo di sekitarnya (mis. `curl | bash`
#: lewat `install.sh` di root). Kosong = ambil dari remote default.
SRC_REPO="${WTH_REPO:-}"
SRC_REF="${WTH_REF:-}"

log()  { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*" >&2; }
die()  { printf '\n[x] %s\n' "$*" >&2; exit 1; }

# Jalankan perintah, atau cetak saja saat --dry-run.
run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '  $ %s\n' "$*"
    return 0
  fi
  "$@"
}

usage() {
  cat <<EOF
WTH-AI installer v$VERSION

Pemakaian:
  scripts/install.sh [opsi]

Opsi:
  --home DIR        workspace WTH_HOME          (default: \$WTH_HOME atau ~/.wth-ai)
  --bin DIR         direktori symlink 'wth'     (default: ~/.local/bin)
  --source DIR      sumber repo/paket           (default: induk skrip ini)
  --repo OWNER/NAME sumber GitHub bila tidak ada checkout lokal
  --ref BRANCH      branch/tag untuk --repo     (default: big-dix)
  --python BIN      interpreter Py >= 3.10  (default: python3)
  --venv / --no-venv  venv terisolasi (default: venv) atau pasang ke interpreter
  --with LIST       fitur opsional: tui,bot,browser,full (pisahkan koma)
  --no-setup        jangan buka wizard setelah instalasi
  --dry-run         cetak perintah, tidak mengubah apa pun
  --yes             jangan minta konfirmasi (dipakai --purge)
  --uninstall       hapus venv + symlink (workspace tetap)
  --purge           seperti --uninstall, lalu hapus WTH_HOME
  -h, --help        bantuan ini

Contoh:
  scripts/install.sh --with tui
  WTH_HOME=/opt/wth scripts/install.sh --bin /usr/local/bin
  scripts/install.sh --uninstall --purge --yes
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --home)        WTH_HOME="$2"; shift 2 ;;
    --home=*)      WTH_HOME="${1#*=}"; shift ;;
    --bin)         BIN_DIR="$2"; shift 2 ;;
    --bin=*)       BIN_DIR="${1#*=}"; shift ;;
    --source)      SRC_DIR="$2"; shift 2 ;;
    --source=*)    SRC_DIR="${1#*=}"; shift ;;
    --repo)        SRC_REPO="$2"; shift 2 ;;
    --repo=*)      SRC_REPO="${1#*=}"; shift ;;
    --ref)         SRC_REF="$2"; shift 2 ;;
    --ref=*)       SRC_REF="${1#*=}"; shift ;;
    --python)      PYTHON="$2"; shift 2 ;;
    --python=*)    PYTHON="${1#*=}"; shift ;;
    --with)        FEATURES="$2"; shift 2 ;;
    --with=*)      FEATURES="${1#*=}"; shift ;;
    --no-setup)    DO_SETUP=0; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    --yes|-y)      YES=1; shift ;;
    --uninstall)   DO_UNINSTALL=1; shift ;;
    --purge)       DO_UNINSTALL=1; PURGE=1; shift ;;
    --venv)        USE_VENV=1; shift ;;
    --no-venv)     USE_VENV=0; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "opsi tidak dikenal: $1 (lihat --help)" ;;
  esac
done

export WTH_HOME
VENV="$WTH_HOME/venv"

# --------------------------------------------------------------------------- #
# uninstall
# --------------------------------------------------------------------------- #
if [[ "$DO_UNINSTALL" == "1" ]]; then
  step "Uninstall WTH-AI"
  for name in wth wth-ai; do
    target="$BIN_DIR/$name"
    if [[ -L "$target" || -e "$target" ]]; then
      log "  hapus $target"
      run rm -f "$target"
    fi
  done
  if [[ -d "$VENV" ]]; then
    log "  hapus venv $VENV"
    run rm -rf "$VENV"
  fi
  if [[ "$PURGE" == "1" ]]; then
    if [[ -d "$WTH_HOME" ]]; then
      if [[ "$YES" != "1" && "$DRY_RUN" != "1" ]]; then
        printf '  Hapus seluruh workspace %s (config, vault, sessions)? [y/N] ' "$WTH_HOME"
        read -r answer || answer=""
        [[ "$answer" =~ ^[Yy]$ ]] || die "dibatalkan — workspace tidak dihapus"
      fi
      log "  hapus workspace $WTH_HOME"
      run rm -rf "$WTH_HOME"
    fi
  else
    log "  workspace $WTH_HOME dipertahankan (pakai --purge untuk menghapusnya)"
  fi
  printf '\n✓ selesai.\n'
  exit 0
fi

# --------------------------------------------------------------------------- #
# 1. python
# --------------------------------------------------------------------------- #
step "Cek Python"
command -v "$PYTHON" >/dev/null 2>&1 || die "$PYTHON tidak ditemukan. Pasang Py >= 3.10 atau pakai --python."
PYVER="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")"
log "  $PYTHON ($PYVER)"
"$PYTHON" - <<'PY' || die "butuh Py >= 3.10"
import sys
raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)
PY

# --------------------------------------------------------------------------- #
# 2. sumber
# --------------------------------------------------------------------------- #
step "Cek sumber"
SRC_DIR="$(cd "$SRC_DIR" 2>/dev/null && pwd || true)"
if [[ -n "$SRC_DIR" && -f "$SRC_DIR/pyproject.toml" ]]; then
  log "  $SRC_DIR"
else
  # Tidak ada checkout di dekat skrip (kasus `curl | bash`) atau sumber yang
  # diberikan tidak valid. Selama ini skrip berhenti di sini; daripada menyuruh
  # operator memahami error, ambil repo sekali ke direktori cache lalu pakai.
  SRC_REPO="${SRC_REPO:-net1nfect/wth-ai}"
  SRC_REF="${SRC_REF:-big-dix}"
  CACHE_DIR="$WTH_HOME/src/$(printf '%s' "$SRC_REPO" | tr '/' '-')"
  # `--repo` menerima `owner/name` (GitHub) atau URL/path git lengkap, supaya
  # mirror/self-hosted dan pengujian lokal memakai jalur kode yang sama.
  case "$SRC_REPO" in
    *://*|/*|./*|../*) CLONE_URL="$SRC_REPO" ;;
    *)                 CLONE_URL="https://github.com/${SRC_REPO}.git" ;;
  esac

  log "  tidak ada checkout lokal — pakai ${SRC_REPO}@${SRC_REF}"
  log "  cache: $CACHE_DIR"
  if ! command -v git >/dev/null 2>&1; then
    die "git tidak ditemukan. Pasang git, atau unduh repo dan jalankan --source DIR"
  fi
  if [[ -d "$CACHE_DIR/.git" ]]; then
    run git -C "$CACHE_DIR" fetch --depth 1 origin "$SRC_REF"
    run git -C "$CACHE_DIR" checkout --detach FETCH_HEAD
  else
    run mkdir -p "$(dirname "$CACHE_DIR")"
    run git clone --depth 1 --branch "$SRC_REF" "$CLONE_URL" "$CACHE_DIR"
  fi
  SRC_DIR="$CACHE_DIR"
  [[ "$DRY_RUN" == "1" ]] || [[ -f "$SRC_DIR/pyproject.toml" ]] \
    || die "clone selesai tapi pyproject.toml tidak ada di $SRC_DIR (ref salah?)"
  log "  $SRC_DIR"
fi

# --------------------------------------------------------------------------- #
# 3. venv
# --------------------------------------------------------------------------- #
if [[ "$USE_VENV" == "1" ]]; then
  step "Virtualenv"
  if [[ -x "$VENV/bin/python" ]]; then
    log "  memakai yang sudah ada: $VENV"
  else
    log "  membuat: $VENV"
    run mkdir -p "$WTH_HOME"
    run "$PYTHON" -m venv "$VENV"
  fi
  PY="$VENV/bin/python"
  PIP=("$PY" -m pip install)
else
  PY="$PYTHON"
  PIP=("$PY" -m pip install --user)
  warn "--no-venv: dependensi masuk ke interpreter pengguna, bukan terisolasi"
fi

# --------------------------------------------------------------------------- #
# 4. paket
# --------------------------------------------------------------------------- #
step "Pasang paket"
run "${PIP[@]}" --quiet --upgrade pip
run "${PIP[@]}" --quiet -e "$SRC_DIR"

if [[ -n "$FEATURES" ]]; then
  IFS=',' read -r -a feature_list <<< "$FEATURES"
  for feature in "${feature_list[@]}"; do
    feature="$(printf '%s' "$feature" | tr -d '[:space:]')"
    [[ -z "$feature" ]] && continue
    case "$feature" in
      "tui")     req="textual>=0.80" ;;
      "bot")     req="discord.py>=2.3 python-telegram-bot>=21.0" ;;
      "browser") req="nodriver>=0.38" ;;
      "full")    req="aiohttp>=3.9 beautifulsoup4>=4.12 lxml>=5.0 textual>=0.80" ;;
      *)         die "fitur tidak dikenal: $feature (pilih: tui,bot,browser,full)" ;;
    esac
    log "  fitur $feature: $req"
    # shellcheck disable=SC2086
    run "${PIP[@]}" --quiet $req
  done
fi

# --------------------------------------------------------------------------- #
# 5. entry point
# --------------------------------------------------------------------------- #
step "Entry point"
ENTRY="$VENV/bin/wth"
[[ "$USE_VENV" == "1" ]] || ENTRY="$(command -v wth || true)"
if [[ -z "${ENTRY:-}" && "$DRY_RUN" != "1" ]]; then
  die "entry point 'wth' tidak ditemukan setelah instalasi"
fi

run mkdir -p "$BIN_DIR"
for name in wth wth-ai; do
  source_path="$VENV/bin/$name"
  [[ "$USE_VENV" == "1" ]] || source_path="$(command -v "$name" || true)"
  [[ -n "$source_path" ]] || continue
  run ln -sf "$source_path" "$BIN_DIR/$name"
done
log "  $BIN_DIR/wth -> $VENV/bin/wth"

# --------------------------------------------------------------------------- #
# 6. workspace
# --------------------------------------------------------------------------- #
step "Workspace"
run "$PY" -c 'from wth_ai.workspace import init_workspace; print(init_workspace())'

# --------------------------------------------------------------------------- #
# 7. verifikasi
# --------------------------------------------------------------------------- #
step "Verifikasi"
if [[ "$DRY_RUN" != "1" ]]; then
  "$ENTRY" --version || die "entry point gagal dijalankan"
  "$ENTRY" setup --check || warn "preflight melaporkan masalah — jalankan: wth setup --check"
else
  log "  (dry-run) lewati eksekusi --version dan setup --check"
fi

# --------------------------------------------------------------------------- #
# 8. PATH + setup
# --------------------------------------------------------------------------- #
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR belum ada di PATH. Tambahkan: export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

step "Selesai"
log "  paket   : $SRC_DIR"
[[ "$USE_VENV" == "1" ]] && log "  venv    : $VENV"
log "  WTH_HOME: $WTH_HOME"
log "  biner   : $BIN_DIR/wth"

if [[ "$DO_SETUP" == "1" ]]; then
  if [[ -t 0 && -t 1 && "$DRY_RUN" != "1" ]]; then
    step "Setup awal"
    exec "$ENTRY" setup
  else
    printf '\n  Langkah berikutnya:\n'
    log "    wth setup            # wizard provider + model"
    log "    wth setup --check    # hanya preflight"
    log "    wth                  # buka REPL"
  fi
else
  printf '\n  Wizard dilewati (--no-setup). Jalankan: wth setup\n'
fi