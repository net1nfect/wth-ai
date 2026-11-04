#!/usr/bin/env bash
# Installer WTH-AI — venv terisolasi, entry point `wth`, workspace ~/.wth-ai.
#
# Kenapa skrip dan bukan sekadar `pip install .`:
#   * `wth` perlu di PATH tanpa mengotori Python sistem.
#   * workspace (~/.wth-ai) punya permission khusus (vault 600, dir 700).
#   * install harus bisa diulang (upgrade) dan dibatalkan (--uninstall).
#   * installer harus memberi tahu APA yang kurang, bukan gagal dengan traceback.
#
# Sudo: TIDAK dipakai. Semua dependensi Python masuk venv di $WTH_HOME. Kalau
# sebuah paket sistem memang hilang (mis. python3-venv di Debian), skrip
# mencetak perintah paket-manager yang tepat lalu berhenti — keputusan memakai
# sudo ada di tangan operator, bukan di dalam installer. `--install-system-deps`
# menjalankan perintah itu hanya bila diminta secara eksplisit.
#
# Kontrak untuk pembungkus (install.sh satu baris di root):
#   * exit 0 = terpasang, 1 = gagal, 2 = argumen salah
#   * stdout ramah terminal; detail teknis ke $WTH_HOME/logs/install.log
set -euo pipefail

VERSION="1.0.0"
SCRIPT_DIR=""

WTH_HOME="${WTH_HOME:-$HOME/.wth-ai}"
BIN_DIR="${WTH_BIN_DIR:-$HOME/.local/bin}"
PYTHON="${WTH_PYTHON:-python3}"
#: True bila operator menyebut interpreter tertentu; lihat find_python().
PYTHON_EXPLICIT=0
[[ -n "${WTH_PYTHON:-}" ]] && PYTHON_EXPLICIT=1
FEATURES=""
DO_SETUP=auto
DO_UNINSTALL=0
PURGE=0
DRY_RUN=0
YES=0
USE_VENV=1
VERBOSE=0
WIRE_PATH=1
PATH_WIRED=0
SYSTEM_DEPS=print
SRC_REPO="${WTH_REPO:-}"
SRC_REF="${WTH_REF:-}"
FAILURE_CLASS=""

# Ada dua cara skrip ini dipanggil:
#   * dari checkout (`scripts/install.sh`) -> sumber = induk skrip ini;
#   * lewat pipa/`bash -s` (bootstrap satu baris) -> tidak ada file di disk,
#     jadi $0 bukan path yang bisa dipakai. Gunakan $PWD, lalu --repo.
if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "-" && "${BASH_SOURCE[0]}" != "bash" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || printf '%s' "$PWD")"
else
  SCRIPT_DIR="$PWD"
fi
SRC_DIR="${WTH_SRC:-$SCRIPT_DIR/..}"

# --------------------------------------------------------------------------- #
# tampilan
# --------------------------------------------------------------------------- #
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RED=$'\033[31m' C_GREEN=$'\033[32m' C_YELLOW=$'\033[33m'
  C_CYAN=$'\033[36m' C_DIM=$'\033[2m' C_BOLD=$'\033[1m' C_NC=$'\033[0m'
else
  C_RED="" C_GREEN="" C_YELLOW="" C_CYAN="" C_DIM="" C_BOLD="" C_NC=""
fi

log()  { printf '%s→%s %s\n' "$C_CYAN" "$C_NC" "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_GREEN" "$C_NC" "$*"; }
warn() { printf '%s!%s %s\n' "$C_YELLOW" "$C_NC" "$*" >&2; }
skip() { printf '%s·%s %s\n' "$C_DIM" "$C_NC" "$*"; }
step() { printf '\n%s==>%s %s%s\n' "$C_CYAN" "$C_NC" "$C_BOLD" "$1${C_NC}"; }

# Kegagalan membawa "kelas" yang stabil supaya pembungkus/CI bisa membedakan
# sebabnya tanpa mem-parse kalimat manusia ("python terlalu lama" vs "jaringan").
fail() {
  FAILURE_CLASS="${2:-other}"
  printf '%s✗%s %s\n' "$C_RED" "$C_NC" "$1" >&2
  printf '  kelas: %s\n' "$FAILURE_CLASS" >&2
  # Workspace bisa saja sudah dihapus (mis. --purge yang gagal di tengah), jadi
  # keberadaan log diperiksa dengan aman — bukan `[[ -f ]] &&` yang bergantung
  # pada status terakhir.
  if [[ -f "$LOG_FILE" ]]; then printf '  log  : %s\n' "$LOG_FILE" >&2; fi
  exit 1
}

usage() {
  cat <<EOF
WTH-AI installer v$VERSION

Pemakaian:
  scripts/install.sh [opsi]

Opsi:
  --home DIR        workspace WTH_HOME          (default: \$WTH_HOME atau ~/.wth-ai)
  --bin DIR         direktori command 'wth'     (default: ~/.local/bin)
  --source DIR      sumber repo/paket           (default: induk skrip ini)
  --repo OWNER/NAME sumber GitHub bila tidak ada checkout lokal
  --ref BRANCH      branch/tag untuk --repo     (default: big-dix)
  --python BIN      interpreter Python >= 3.10  (default: python3, dicari 3.13..3.10)
  --venv / --no-venv  venv terisolasi (default) atau pasang ke interpreter pengguna
  --with LIST       fitur opsional: tui,bot,browser,full (pisahkan koma)
  --no-setup        jangan buka wizard setelah instalasi
  --no-path         jangan sentuh file rc shell (hanya cetak instruksi PATH)
  --system-deps MODE  cetak|jalankan  (default: cetak) — perintah paket sistem
                   yang kurang, mis. python3-venv. 'jalankan' memakai sudo dan
                   HANYA dijalankan bila Anda memintanya.
  --dry-run         cetak perintah, tidak mengubah apa pun
  --verbose         tampilkan output setiap perintah (default: hanya saat gagal)
  --yes             jangan minta konfirmasi (dipakai --purge)
  --uninstall       hapus venv + command + baris PATH (workspace tetap)
  --purge           seperti --uninstall, lalu hapus WTH_HOME
  -h, --help        bantuan ini

Sudo tidak dipakai untuk pemasangan normal: dependensi Python masuk venv
\$WTH_HOME/venv. Butuh Python >= 3.10, dan git hanya bila sumber di-clone.

Contoh:
  scripts/install.sh --with tui
  WTH_HOME=/opt/wth scripts/install.sh --bin /usr/local/bin
  scripts/install.sh --dry-run
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
    --python)      PYTHON="$2"; PYTHON_EXPLICIT=1; shift 2 ;;
    --python=*)    PYTHON="${1#*=}"; PYTHON_EXPLICIT=1; shift ;;
    --with)        FEATURES="$2"; shift 2 ;;
    --with=*)      FEATURES="${1#*=}"; shift ;;
    --no-setup)    DO_SETUP=0; shift ;;
    --no-path)     WIRE_PATH=0; shift ;;
    --system-deps) SYSTEM_DEPS="${2:-print}"; shift 2 ;;
    --system-deps=*) SYSTEM_DEPS="${1#*=}"; shift ;;
    --install-system-deps) SYSTEM_DEPS=run; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    --verbose)     VERBOSE=1; shift ;;
    --yes|-y)      YES=1; shift ;;
    --uninstall)   DO_UNINSTALL=1; shift ;;
    --purge)       DO_UNINSTALL=1; PURGE=1; shift ;;
    --venv)        USE_VENV=1; shift ;;
    --no-venv)     USE_VENV=0; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             printf 'opsi tidak dikenal: %s (lihat --help)\n' "$1" >&2; exit 2 ;;
  esac
done

case "$SYSTEM_DEPS" in
  cetak|print) SYSTEM_DEPS=print ;;
  jalankan|run) SYSTEM_DEPS=run ;;
  *) printf -- '--system-deps harus cetak|jalankan\n' >&2; exit 2 ;;
esac

export WTH_HOME
VENV="$WTH_HOME/venv"
LOG_DIR="$WTH_HOME/logs"
LOG_FILE="$LOG_DIR/install.log"
# Opsi ini milik `pip install`, bukan `pip`, jadi selalu diletakkan SETELAH
# subcommand install.
PIP_EXTRA=(--quiet --disable-pip-version-check --no-input --no-warn-script-location)

# --------------------------------------------------------------------------- #
# util: jalankan + catat
# --------------------------------------------------------------------------- #
# Semua perintah anak lewat sini: output penuh ke log, layar hanya bila gagal
# atau --verbose. Tanpa ini, error pip yang sebenarnya (satu baris di antara
# ratusan) hilang di balik "exit 1".
# Log bersifat best-effort: `--uninstall --purge` menghapus $WTH_HOME, dan
# perintah di dalamnya tetap harus jalan walau berkas log sudah tidak ada.
_log_write() {
  if [[ -d "$LOG_DIR" ]]; then
    printf '%s\n' "$1" >>"$LOG_FILE" 2>/dev/null || true
  fi
  return 0
}

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '  %s$%s %s\n' "$C_DIM" "$C_NC" "$*"
    return 0
  fi
  _log_write "\$ $*"
  local output status
  if output="$("$@" 2>&1)"; then
    status=0
  else
    status=$?
  fi
  _log_write "$output"
  if [[ $status -ne 0 ]]; then
    printf '%s\n' "$output" | tail -n 20 >&2
    return $status
  fi
  if [[ "$VERBOSE" == "1" && -n "$output" ]]; then
    printf '%s\n' "$output"
  fi
  return 0
}

mkdir -p "$LOG_DIR" 2>/dev/null || true
# Log instalasi tetap ditulis walau --dry-run: itu satu-satunya bukti apa yang
# akan dijalankan. Yang dijaga justru sebaliknya — dry-run tidak boleh
# menyentuh workspace, venv, atau bin.
if [[ "$DRY_RUN" != "1" ]]; then
  printf '\n=== WTH-AI install %s ===\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$LOG_FILE"
fi

# --------------------------------------------------------------------------- #
# deteksi host + dependensi sistem (tanpa sudo)
# --------------------------------------------------------------------------- #
OS_FAMILY="unknown"
detect_os() {
  case "$(uname -s 2>/dev/null)" in
    Darwin) OS_FAMILY=macos; return ;;
    Linux)
      if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        case "${ID:-}${ID_LIKE:-}" in
          *debian*|*ubuntu*) OS_FAMILY=debian ;;
          *fedora*|*rhel*|*centos*) OS_FAMILY=fedora ;;
          *arch*) OS_FAMILY=arch ;;
          *alpine*) OS_FAMILY=alpine ;;
          *) OS_FAMILY=linux ;;
        esac
      else
        OS_FAMILY=linux
      fi
      return ;;
    *) OS_FAMILY=unknown ;;
  esac
}

# Perintah paket-manager untuk satu paket, per distro. DICETAK, tidak dijalankan.
pkg_hint() {
  local pkg="$1"
  case "$OS_FAMILY" in
    debian) printf 'sudo apt-get install -y %s' "$pkg" ;;
    fedora) printf 'sudo dnf install -y %s' "$pkg" ;;
    arch)   printf 'sudo pacman -S --needed %s' "$pkg" ;;
    alpine) printf 'sudo apk add %s' "$pkg" ;;
    macos)  printf 'brew install %s' "$pkg" ;;
    *)      printf '# pasang paket %s dengan package manager Anda' "$pkg" ;;
  esac
}

# Nama paket yang menyediakan modul venv, per distro.
venv_pkg() {
  case "$OS_FAMILY" in
    debian) printf 'python3-venv' ;;
    fedora) printf 'python3' ;;
    arch)   printf 'python' ;;
    alpine) printf 'python3' ;;
    *)      printf 'python3-venv' ;;
  esac
}

# Cari interpreter >= 3.10: $PYTHON, lalu python3.13..python3.10 di PATH.
# Debian tanpa `python3-venv` tetap punya python3, jadi versi saja tidak cukup —
# yang diuji adalah `-m venv` benar-benar bisa membuat environment.
PY=""
PY_VERSION=""
find_python() {
  local candidate
  # Interpreter yang disebut eksplisit tidak boleh diam-diam diganti: operator
  # yang menunjuk python tertentu harus tahu kalau versinya tidak memenuhi.
  if [[ "$PYTHON_EXPLICIT" == "1" ]]; then
    command -v "$PYTHON" >/dev/null 2>&1 || return 1
    PY_VERSION="$("$PYTHON" -c 'import sys; sys.stdout.write("%d.%d" % sys.version_info[:2]) if sys.version_info[:2] >= (3, 10) else sys.exit(1)' 2>/dev/null)" || return 1
    PY="$(command -v "$PYTHON")"
    return 0
  fi
  for candidate in python3 python3.13 python3.12 python3.11 python3.10 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    # Perbandingan versi lewat interpreter-nya sendiri: `sort -V` tidak ada di
    # sort bawaan macOS, dan mengandalkannya membuat installer gagal di sana.
    PY_VERSION="$("$candidate" -c 'import sys; sys.stdout.write("%d.%d" % sys.version_info[:2]) if sys.version_info[:2] >= (3, 10) else sys.exit(1)' 2>/dev/null)" || continue
    PY="$(command -v "$candidate")"
    return 0
  done
  return 1
}

venv_usable() {
  "$1" -c 'import ensurepip, venv' >/dev/null 2>&1
}

run_system_deps() {
  local pkg="$1"
  local cmd
  cmd="$(pkg_hint "$pkg")"
  if [[ "$SYSTEM_DEPS" != "run" ]]; then
    warn "paket sistem kurang: $pkg"
    printf '    %s\n' "$cmd"
    printf '    lalu jalankan ulang installer, atau tambahkan: --system-deps jalankan\n'
    return 1
  fi
  command -v sudo >/dev/null 2>&1 || fail "butuh $pkg tetapi sudo tidak ada; jalankan sendiri: $cmd" system_deps
  log "memasang $pkg (diminta lewat --system-deps jalankan)"
  # shellcheck disable=SC2086  # cmd sengaja dipecah menjadi argv
  run $cmd || fail "gagal memasang $pkg; jalankan manual: $cmd" system_deps
}

# --------------------------------------------------------------------------- #
# uninstall
# --------------------------------------------------------------------------- #
PATH_MARKER="# WTH-AI command"
# shellcheck disable=SC2016  # $PATH sengaja literal: dievaluasi di rc file
PATH_LINE='case ":$PATH:" in *":'"$BIN_DIR"':"*) ;; *) export PATH="'"$BIN_DIR"':$PATH" ;; esac'

remove_shell_path() {
  local rc
  for rc in "$HOME/.bashrc" "$HOME/.profile" "$HOME/.bash_profile" "$HOME/.bash_login" \
            "$HOME/.zshrc" "$HOME/.zprofile" "$HOME/.config/fish/config.fish"; do
    [[ -f "$rc" ]] || continue
    grep -qF "$PATH_MARKER" "$rc" || continue
    log "  hapus baris PATH dari $rc"
    if [[ "$DRY_RUN" == "1" ]]; then
      skip "(dry-run) menyunting $rc"
      continue
    fi
    # Buang marker + baris setelahnya, sisanya utuh.
    local tmp="$rc.wth.$$"
    awk -v marker="$PATH_MARKER" '
      $0 == marker { getline; next }
      { print }
    ' "$rc" >"$tmp" && mv "$tmp" "$rc"
  done
}

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
  [[ "$WIRE_PATH" == "1" ]] && remove_shell_path
  if [[ "$PURGE" == "1" ]]; then
    if [[ -d "$WTH_HOME" ]]; then
      if [[ "$YES" != "1" && "$DRY_RUN" != "1" ]]; then
        printf '  Hapus seluruh workspace %s (config, vault, sessions)? [y/N] ' "$WTH_HOME"
        read -r answer || answer=""
        [[ "$answer" =~ ^[Yy]$ ]] || fail "dibatalkan — workspace tidak dihapus" cancelled
      fi
      log "  hapus workspace $WTH_HOME"
      run rm -rf "$WTH_HOME"
    fi
  else
    skip "workspace $WTH_HOME dipertahankan (--purge untuk menghapusnya)"
  fi
  ok "uninstall selesai"
  exit 0
fi

# --------------------------------------------------------------------------- #
# 1. Python + venv
# --------------------------------------------------------------------------- #
step "Dependensi sistem"
detect_os
log "host: $(uname -s 2>/dev/null) $(uname -m 2>/dev/null) · keluarga: $OS_FAMILY"

if ! find_python; then
  if [[ "$PYTHON_EXPLICIT" == "1" ]]; then
    warn "interpreter '${PYTHON}' tidak memenuhi syarat (butuh Python >= 3.10)"
    printf '    %s\n' "$(pkg_hint python3)"
    fail "Python >= 3.10 tidak ditemukan pada interpreter yang diminta (--python $PYTHON)" python_missing
  fi
  warn "butuh Python >= 3.10, tidak ada yang cocok di PATH"
  printf '    %s\n' "$(pkg_hint python3)"
  run_system_deps python3 || true
  fail "Python >= 3.10 tidak ditemukan (butuh 3.10+)" python_missing
fi
ok "python $PY_VERSION ($PY)"

if [[ "$USE_VENV" == "1" ]] && ! venv_usable "$PY"; then
  warn "modul 'venv'/'ensurepip' tidak tersedia di $PY"
  run_system_deps "$(venv_pkg)" || true
  fail "venv tidak bisa dibuat. Pasang $(venv_pkg), atau jalankan ulang dengan --no-venv" venv_missing
fi

FREE_MB="$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {print int($4/1024)}' || true)"
if [[ -n "$FREE_MB" && "$FREE_MB" =~ ^[0-9]+$ && "$FREE_MB" -lt 500 ]]; then
  warn "ruang disk tersisa ${FREE_MB} MB; venv + cache pip butuh ~500 MB"
fi

# --------------------------------------------------------------------------- #
# 2. perintah pendukung
# --------------------------------------------------------------------------- #
step "Perintah pendukung"
if command -v curl >/dev/null 2>&1; then ok "curl"; else warn "curl tidak ada (hanya dibutuhkan bootstrap satu baris)"; fi
# git hanya wajib bila sumber harus di-clone; checkout lokal tidak memerlukannya.
NEED_GIT=0

# --------------------------------------------------------------------------- #
# 3. sumber
# --------------------------------------------------------------------------- #
step "Sumber"
SRC_DIR="$(cd "$SRC_DIR" 2>/dev/null && pwd || true)"
if [[ -n "$SRC_DIR" && -f "$SRC_DIR/pyproject.toml" ]]; then
  ok "$SRC_DIR"
else
  NEED_GIT=1
  SRC_REPO="${SRC_REPO:-net1nfect/wth-ai}"
  SRC_REF="${SRC_REF:-big-dix}"
  CACHE_DIR="$WTH_HOME/src/$(printf '%s' "$SRC_REPO" | tr '/:' '--')"
  # `--repo` menerima `owner/name` (GitHub) atau URL/path git lengkap, supaya
  # mirror/self-hosted dan pengujian lokal memakai jalur kode yang sama.
  case "$SRC_REPO" in
    *://*|/*|./*|../*) CLONE_URL="$SRC_REPO" ;;
    *)                 CLONE_URL="https://github.com/${SRC_REPO}.git" ;;
  esac
  log "tidak ada checkout lokal — pakai ${SRC_REPO}@${SRC_REF}"
  log "cache: $CACHE_DIR"
  if [[ -d "$CACHE_DIR/.git" ]]; then
    run git -C "$CACHE_DIR" fetch --depth 1 origin "$SRC_REF" \
      || fail "gagal fetch $SRC_REF dari $CLONE_URL" git_fetch_failed
    run git -C "$CACHE_DIR" checkout --detach FETCH_HEAD \
      || fail "gagal checkout FETCH_HEAD" git_checkout_failed
  else
    run mkdir -p "$(dirname "$CACHE_DIR")"
    run git clone --depth 1 --branch "$SRC_REF" "$CLONE_URL" "$CACHE_DIR" \
      || fail "gagal clone $CLONE_URL ($SRC_REF)" git_clone_failed
  fi
  SRC_DIR="$CACHE_DIR"
  [[ "$DRY_RUN" == "1" || -f "$SRC_DIR/pyproject.toml" ]] \
    || fail "clone selesai tapi pyproject.toml tidak ada (ref salah?)" source_invalid
  ok "$SRC_DIR"
fi

if [[ "$NEED_GIT" == "1" ]] && ! command -v git >/dev/null 2>&1; then
  run_system_deps git || true
  fail "git dibutuhkan untuk mengambil sumber" git_missing
fi

# --------------------------------------------------------------------------- #
# 4. venv
# --------------------------------------------------------------------------- #
if [[ "$USE_VENV" == "1" ]]; then
  step "Virtualenv"
  if [[ -x "$VENV/bin/python" ]]; then
    ok "memakai yang sudah ada: $VENV"
  else
    log "membuat: $VENV"
    run mkdir -p "$WTH_HOME" || fail "tidak bisa membuat $WTH_HOME" filesystem_error
    if ! run "$PY" -m venv "$VENV"; then
      rm -rf "$VENV" 2>/dev/null || true
      warn "pembuatan venv gagal"
      run_system_deps "$(venv_pkg)" || true
      fail "venv tidak bisa dibuat di $VENV" venv_missing
    fi
    ok "venv dibuat"
  fi
  PY="$VENV/bin/python"
  PIP=("$PY" -m pip)
  PIP_USER=()
else
  step "Virtualenv"
  warn "--no-venv: dependensi masuk ke interpreter pengguna, bukan terisolasi"
  # PEP 668: Debian/Fedora menandai interpreter sistem sebagai
  # externally-managed. pip menolaknya dengan pesan panjang yang mudah
  # disalahartikan sebagai bug installer.
  if "$PY" -c 'import pathlib, sysconfig; raise SystemExit(0 if (pathlib.Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").exists() else 1)' 2>/dev/null; then
    fail "host ini externally-managed (PEP 668): pip --user ditolak. Jalankan tanpa --no-venv (default), atau pakai pipx" pep668
  fi
  PIP=("$PY" -m pip)
  PIP_USER=(--user)
fi

if [[ "$DRY_RUN" != "1" ]] && ! "$PY" -m pip --version >/dev/null 2>&1; then
  fail "pip tidak berjalan di $PY (cek: $PY -m pip --version)" pip_missing
fi

# --------------------------------------------------------------------------- #
# 5. paket
# --------------------------------------------------------------------------- #
step "Pasang paket"
run "${PIP[@]}" install "${PIP_USER[@]}" "${PIP_EXTRA[@]}" -e "$SRC_DIR" \
  || fail "pip install -e $SRC_DIR gagal (lihat log)" deps_install_failed

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
      *)         fail "fitur tidak dikenal: $feature (pilih: tui,bot,browser,full)" bad_feature ;;
    esac
    log "fitur $feature: $req"
    # shellcheck disable=SC2086
    run "${PIP[@]}" install "${PIP_USER[@]}" "${PIP_EXTRA[@]}" $req \
      || fail "gagal memasang fitur $feature" deps_install_failed
  done
fi

# --------------------------------------------------------------------------- #
# 6. command
# --------------------------------------------------------------------------- #
step "Command"
# Entry point di venv selalu ada setelah pip install -e; --no-venv bergantung
# pada direktori scripts milik interpreter pengguna yang mungkin belum di PATH.
if [[ "$USE_VENV" == "1" ]]; then
  ENTRY="$VENV/bin/wth"
  [[ "$DRY_RUN" == "1" || -x "$ENTRY" ]] || fail "entry point tidak dibuat: $ENTRY" entry_missing
else
  ENTRY="$(command -v wth || true)"
  [[ -n "$ENTRY" || "$DRY_RUN" == "1" ]] || fail "entry point 'wth' tidak ada di PATH" entry_missing
fi

run mkdir -p "$BIN_DIR" || fail "tidak bisa membuat $BIN_DIR" filesystem_error
for name in wth wth-ai; do
  source_path="$VENV/bin/$name"
  [[ "$USE_VENV" == "1" ]] || source_path="$(command -v "$name" || true)"
  [[ -n "$source_path" ]] || continue
  if [[ -e "$BIN_DIR/$name" && ! -L "$BIN_DIR/$name" ]]; then
    # Jangan menimpa biner asing tanpa jejak.
    warn "$BIN_DIR/$name sudah ada dan bukan symlink; dilewati"
    continue
  fi
  run ln -sf "$source_path" "$BIN_DIR/$name" || fail "gagal symlink $BIN_DIR/$name" filesystem_error
done
ok "$BIN_DIR/wth -> $VENV/bin/wth"

# --------------------------------------------------------------------------- #
# 7. PATH (idempotent, sadar login/non-login shell)
# --------------------------------------------------------------------------- #
# Baris dikunci marker dan dijaga `case`, supaya login shell yang membaca
# .bashrc DAN .profile tidak menaruh $BIN_DIR dua kali.
wire_shell_path() {
  local rc
  case "${SHELL:-/bin/bash}" in
    *zsh)
      add_shell_path "$HOME/.zshrc"; add_shell_path "$HOME/.zprofile" ;;
    *fish)
      rc="$HOME/.config/fish/config.fish"
      if [[ -f "$rc" ]] && grep -qF 'fish_add_path' "$rc" && grep -qF "$BIN_DIR" "$rc"; then
        return 0
      fi
      if [[ "$DRY_RUN" == "1" ]]; then
        skip "(dry-run) tambah $BIN_DIR ke $rc"
        return 0
      fi
      mkdir -p "$(dirname "$rc")"
      printf '\n%s\nfish_add_path "%s"\n' "$PATH_MARKER" "$BIN_DIR" >>"$rc"
      ok "PATH ditambahkan ke $rc"
      return 0 ;;
    *)
      add_shell_path "$HOME/.bashrc"
      add_shell_path "$HOME/.profile"
      # Login bash hanya membaca file pertama yang ada di antara
      # .bash_profile/.bash_login/.profile.
      if [[ -f "$HOME/.bash_profile" ]]; then add_shell_path "$HOME/.bash_profile"; fi
      if [[ -f "$HOME/.bash_login" ]]; then add_shell_path "$HOME/.bash_login"; fi ;;
  esac
  # `set -e` membaca status perintah terakhir: fungsi yang berakhir pada `[[ -f ]]`
  # yang false akan menghentikan seluruh installer tanpa pesan.
  return 0
}

add_shell_path() {
  local rc="$1"
  # Hanya file yang sudah ada yang disentuh, kecuali .bashrc (dibuat bila
  # belum ada — bash selalu membacanya untuk shell interaktif).
  [[ -f "$rc" ]] || { [[ "$rc" == "$HOME/.bashrc" ]] || return 0; }
  if [[ -f "$rc" ]] && grep -qF "$PATH_MARKER" "$rc"; then
    return 0
  fi
  # PATH yang sudah disiapkan pengguna sendiri dihormati.
  if [[ -f "$rc" ]] && grep -qE "PATH=.*$(printf '%s' "$BIN_DIR" | sed 's/[][\\.^$*+?(){}|]/\\&/g')" "$rc"; then
    return 0
  fi
  PATH_WIRED=$((PATH_WIRED + 1))
  if [[ "$DRY_RUN" == "1" ]]; then
    skip "(dry-run) tambah $BIN_DIR ke $rc"
    return 0
  fi
  mkdir -p "$(dirname "$rc")"
  printf '\n%s\n%s\n' "$PATH_MARKER" "$PATH_LINE" >>"$rc" \
    || fail "tidak bisa menulis $rc" filesystem_error
  ok "PATH ditambahkan ke $rc"
}

step "PATH"
if [[ "$WIRE_PATH" == "1" ]]; then
  wire_shell_path
  if [[ "$PATH_WIRED" == "0" ]]; then
    skip "tidak ada file rc ${SHELL##*/} yang bisa ditulis; tambahkan $BIN_DIR ke PATH manual"
  fi
else
  skip "dilewati (--no-path)"
fi
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR belum aktif di shell ini. Jalankan: export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

# --------------------------------------------------------------------------- #
# 8. workspace
# --------------------------------------------------------------------------- #
step "Workspace"
run "$PY" -c 'from wth_ai.workspace import init_workspace; print(init_workspace())' \
  || fail "gagal membuat workspace $WTH_HOME" workspace_failed
ok "$WTH_HOME"

# --------------------------------------------------------------------------- #
# 9. verifikasi
# --------------------------------------------------------------------------- #
step "Verifikasi"
if [[ "$DRY_RUN" == "1" ]]; then
  skip "(dry-run) lewati eksekusi --version dan setup --check"
else
  if version_out="$("$PY" -m wth_ai.cli.entry --version 2>&1)"; then
    printf '%s\n' "$version_out" >>"$LOG_FILE"
    ok "$(printf '%s\n' "$version_out" | tail -n 1)"
  else
    printf '%s\n' "$version_out" | tail -n 20 >&2
    fail "entry point gagal dijalankan" verify_failed
  fi
  if run "$PY" -m wth_ai.cli.entry setup --check; then
    ok "preflight bersih"
  else
    warn "preflight melaporkan masalah — jalankan: wth setup --check"
  fi
fi

# --------------------------------------------------------------------------- #
# 10. ringkasan + setup
# --------------------------------------------------------------------------- #
step "Selesai"
printf '  %-9s %s\n' "paket" "$SRC_DIR"
[[ "$USE_VENV" == "1" ]] && printf '  %-9s %s\n' "venv" "$VENV"
printf '  %-9s %s\n' "WTH_HOME" "$WTH_HOME"
printf '  %-9s %s\n' "command" "$BIN_DIR/wth"
printf '  %-9s %s\n' "log" "$LOG_FILE"

if [[ "$DO_SETUP" == "1" ]]; then
  if [[ -t 0 && -t 1 && "$DRY_RUN" != "1" ]]; then
    step "Setup awal"
    exec "$ENTRY" setup
  else
    printf '\n  Langkah berikutnya:\n'
    printf '    wth setup            # wizard provider + model\n'
    printf '    wth setup --check    # hanya preflight\n'
    printf '    wth                  # buka REPL\n'
  fi
else
  printf '\n  Wizard dilewati (--no-setup). Jalankan: wth setup\n'
fi