#!/bin/sh
# wth-ai one-line bootstrap.
#
#   curl -fsSL https://raw.githubusercontent.com/net1nfect/wth-ai/big-dix/install.sh | bash
#
# Why a POSIX sh file this small exists at all: `curl | sh` runs the script
# stdin, so nothing relative to it can be resolved. It fetches the real installer
# into a temp dir, runs it there, then cleans up. The bucket name and branch can
# be overridden, which is what makes the same line usable for a fork or a tag.
#
# No sudo, no eval of remote content beyond the pinned repo path, and the
# installer keeps its own --dry-run/--uninstall contract.
set -eu

WTH_REPO="${WTH_REPO:-net1nfect/wth-ai}"
#: Branch tempat file ini dipublikasikan. Branch ini diisi saat rilis: memakai
#: `main` sebagai default membuat instalasi dari branch lain (atau fork) 404,
#: atau lebih buruk: memasang kode dari branch yang salah tanpa terlihat.
WTH_REF="${WTH_REF:-big-dix}"
WTH_SRC_URL="${WTH_SRC_URL:-https://raw.githubusercontent.com/${WTH_REPO}/${WTH_REF}/scripts/install.sh}"

fail() {
    printf 'wth-ai bootstrap: %s\n' "$*" >&2
    exit 1
}

command -v curl >/dev/null 2>&1 || fail "curl tidak ditemukan; pasang curl atau unduh scripts/install.sh manual"
command -v sh >/dev/null 2>&1 || fail "sh tidak ditemukan"
# `curl | sh` sudah memakai sh; repo ini butuh bash (array, [[ ), jadi jangan
# bergantung pada /bin/sh yang mungkin dash.
BASH_BIN="$(command -v bash || true)"
[ -n "$BASH_BIN" ] || fail "bash tidak ditemukan (installer butuh bash, bukan sh murni)"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wth-install.XXXXXX" 2>/dev/null)" || fail "tidak bisa membuat direktori sementara"
# shellcheck disable=SC2064  # TMP_DIR harus di-expand sekarang, bukan saat trap jalan
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

INSTALLER="$TMP_DIR/install.sh"
printf 'wth-ai bootstrap: mengambil installer dari %s\n' "$WTH_SRC_URL"
curl -fsSL --retry 2 --connect-timeout 15 -o "$INSTALLER" "$WTH_SRC_URL" \
    || fail "gagal mengunduh installer dari $WTH_SRC_URL (repo/ref salah?)"

# Berkas kosong atau halaman HTML 404 akan membuat `sh` keluar dengan error yang
# membingungkan, jadi bentuknya diperiksa dulu sebelum dijalankan.
[ -s "$INSTALLER" ] || fail "installer kosong: $WTH_SRC_URL"
head -n 1 "$INSTALLER" | grep -q '^#!' || fail "isi $WTH_SRC_URL bukan skrip shell (404/halaman HTML?)"

# `bash "$INSTALLER"` — bukan `sh` — karena installer memakai array dan `[[`.
# Argumen operator (`--with tui`, `--dry-run`) diteruskan utuh; `$0` menunjuk
# file nyata sehingga pesan error menunjuk skrip yang benar.
#
# `exec` membuat exit code installer (0 ok, 1 gagal, 2 argumen salah) menjadi
# exit code pipeline ini juga.
exec "$BASH_BIN" "$INSTALLER" "$@"