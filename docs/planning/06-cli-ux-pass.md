# 06 — CLI UX pass: palette `/`, saran typo, `/help <kategori>`, menu ber-alur

Status: **SELESAI** (2026-11-04). Kode, test, suite, dan scan hijau; ter-push.
Sumber: keluhan operator ("command seperti `/` jangan membingungkan") + audit
langsung ke jalur REPL/router/menu.

## 1. Masalah (semua TERUKUR di kode/sesi nyata)

1. **Bare `/` di mode plain/bot dijawab "Unknown command `/`"** — buntu. Menu
   interaktif memang ada di REPL TTY, tapi jalur plain/gateway (dan test) jatuh
   ke pesan error.
2. **Salah ketik tanpa satu pun saran**: `/rcon` → "Unknown command `...`. Try
   `/help` or Ctrl+P palette." — padahal kasus paling umum adalah typo dekat.
3. **`/help <kategori>` tidak pernah bekerja**: cabang `category` di
   `SlashRouter.help_text()` tidak reachable (untuk topic non-keyword `compact`
   selalu `True`, dan `cat` di-nol-kan begitu `compact` True) — `/help setup`
   diam-diam menampilkan compact biasa.
4. **Menu `/` tanpa titik masuk**: level-0 menampilkan nama kategori polos
   ("hunt", "meta", …) berdeskripsi "N command"; kategori terurut alfabetis
   sehingga "hunt" di atas dan "setup" terkubur.

## 2. Perubahan

* `wth_ai/platform/registry.py`:
  * `CATEGORY_ORDER` + `CATEGORY_DESCRIPTIONS` (urutan alur kerja: setup →
    safety → recon → hunt → …), `START_HERE` (5 langkah pertama yang
    disarankan), `category_title()` (label pendek: "MCP", "Skills").
  * `suggestions()` — saran command terdekat (prefix → substring →
    subsequence `/rcon` → `/recon`), dipakai bersama oleh pesan unknown-command
    DAN completer.
  * `_help_compact()` baru: blok "▶ MULAI DI SINI" + tiap kategori dengan
    deskripsi satu baris + jumlah command + daftar nama; tips bawah.
  * `help_text(kategori)` tak dikenal → palette + daftar kategori valid
    (bukan halaman kosong).
* `wth_ai/cli/slash_router.py`:
  * `route("/")` → palette (bukan error); unknown → `_unknown_command_message()`
    dengan saran "Mungkin maksudmu: …".
  * `_render_help()` — SATU implementasi `/help` untuk router + handler;
    topic non-keyword kini benar-benar dirender sebagai kategori (bug #3).
* `wth_ai/cli/ui/menu.py`: level-0 = "▶ mulai di sini" + kategori berjudul
  pendek berdeskripsi manusia ("petakan attack surface · 8 command"), urutan
  alur kerja.
* `wth_ai/cli/tui/widgets/slash_completer.py`: fallback fuzzy memakai
  `registry.suggestions()` — menu completion dan pesan error menawarkan saran
  yang sama.

## 3. Bukti

`tests/test_slash_ux.py` — **14 test**: bare `/` (router + orchestrator) →
palette tanpa "Unknown command"; typo → saran (`/rcon`→`/recon`,
`/permisson`→`/permission`); `/help recon` benar-benar kategori (usage +
command-nya); `/help bogus` → fallback berisi daftar valid; `/help full` tetap
daftar penuh; compact tetap memuat "Skills"/"MCP"/"/mcp"/"/quit"; menu dimulai
"▶ mulai di sini", kategori berurutan alur, deskripsi manusiawi, anak shortcut
adalah command nyata; completer memberi saran typo.

Test lama yang SENGAJA diperbarui (didokumentasikan): helper `_leaf()` di
`tests/test_cli_menu.py` melewati item shortcut "▶ mulai di sini" — shortcut
adalah jalan pintas, bukan definisi command; tanpa ini ia membayangi
`/hunt → goal` yang asli.

Suite penuh: **1383 passed / 0 failed**; bandit `-ll` clean.

## 4. Sisa (jujur)

* Layar palette di TUI (Ctrl+P) belum memakai `CATEGORY_DESCRIPTIONS` baru —
  menyusul.
* Warna/ikon per kategori di palette plain belum (butuh keputusan tema).
* `/help full` tetap panjang by design (daftar lengkap memang lengkap).
