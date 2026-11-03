# 08 — Bugfix dari transkrip operator: `/resume` kosong, menu hijack, truncation tak terlihat

Status: **SELESAI** (2026-11-04). Kode + 8 test + suite 1402 + bandit hijau; ter-push.
Sumber: transkrip sesi `wth` milik operator — `/resume` tanpa output, `/model`
memunculkan panel "Shortcuts", `/findings` kosong, dan jawaban chat terpotong tepat
di "Rekomendasi next step (pilih satu):".

Metode: reproduksi di **PTY sungguhan** (`wth` + TUI, COLUMNS=120 LINES=45),
log proses penuh, plus instrumentasi provider. Bukan tebakan.

## 1. BUG NYATA #1 — `/resume` membuka menu satu-item, Esc = turn kosong

Reproduksi PTY (log proses):

```
❯ /resume
 /resume · Resume session
 ▶ id                     /resume id
```

Akar: `_subcommands_from_usage("/resume [id]")` menganggap `[id]` sebagai daftar
subcommand → menu satu-item "id"; Esc dari menu itu mengembalikan `None`
("turn dilewati") → layar kosong. Operator melihat "/resume tidak bereaksi".

Perbaikan:
* `menu.py`: grup bracket TANPA `|` = placeholder argumen → `[]`
  (`/resume [id]`, `/tools [args]`), bukan subcommand.
* `repl.py::_maybe_open_menu`: menu hanya dibuka bila ada **≥2** pilihan;
  satu pilihan bukan pertanyaan.

Bukti setelah fix (PTY live, kode editan sama sekali):

```
❯ /resume
╭─────────── Resume Session ───────────╮
│ ... Findings: 0 ...                  │
│ Completed tasks: 0                   │
│ === END RESUME ===                   │
╰──────────────────────────────────────╯
```

## 2. BUG NYATA #2 — `/effort` ke-hijack menu generik (picker deskriptif tak tampil)

`/effort [low|medium|high|xhigh|ultra]` punya 5 "subcommand" → menu generik
`_maybe_open_menu` menangkapnya sebelum handler; picker deskriptif (label
"hemat/panjang/maksimum", sekarang + cancel yang jelas) tak pernah tampil.

Perbaikan: `"/effort"` masuk `SELF_INTERACTIVE` (sama seperti `/model`,
`/permission`) sehingga handler menjalankan picker-nya sendiri.

Bukti live:

```
▶ low     hemat — jawaban ringkas
  medium  default — seimbang
  high    panjang — analisa lebih dalam
  xhigh   berat — output & konteks besar
  ultra   maksimum — pakai saat butuh
 enter select · esc back · ↑/↓ pilih · Enter pasang · Esc batal
```

## 3. BUG NYATA #3 — output terpotong tidak bisa dibedakan dari output selesai

Chat berhenti di tengah daftar; `finish_reason` dari respons provider **dibuang
total** (`openai_compat.chat()` hanya mengembalikan `content`), jadi WTH-AI tidak
tahu — dan tidak bisa memberi tahu — bahwa jawaban terpotong di `max_tokens`.

Perbaikan:
* `OpenAICompatProvider`: rekam `last_finish_reason` (chat + chat_with_tools).
* `LLMProviderRouter.chat_for_role`: teruskan `last_finish_reason` panggilan terakhir.
* `ChatAgent.respond`: bila `finish_reason == "length"`, tempel peringatan
  terlihat — `⚠ Respons terpotong ... Naikkan budget: /effort high (atau ultra)`.

Dengan `/effort` (dok 07) ini menutup lingkaran: gejala terlihat → penyebab
tertulis → satu perintah untuk memperbaiki.

## 4. Yang TIDAK tereproduksi (jujur)

* **Panel "Shortcuts" setelah `/model`**: tidak muncul di PTY asli — `/model`
  membuka picker 14 model (benar). Di seluruh kode, panel "Shortcuts" hanya
  dicetak oleh `_print_shortcuts()`, yang HANYA terikat tombol `?` saat input
  kosong. Dugaan terkuat: tombol `?` tidak sengaja tertekan / artefak
  keypress terminal. Tidak ada perubahan kode untuk ini; kalau terulang,
  langkah persisnya (dan terminal yang dipakai) akan menentukan.
* **`/findings` "kosong"**: di log proses, `/findings` TERCETAK
  (`No findings yet.`) tepat di atas prompt. Blank di layar adalah artefak
  repaint TUI (baris output digambar lalu prompt digambar ulang), bukan output
  hilang. Tidak diubah — menunggu reproduksi yang bisa dipegang.

## 5. Bukti

`tests/test_cli_bugfixes_menu_truncation.py` — **8 test**:
placeholder `[id]` bukan subcommand; pipe-group tetap subcommand; menu tidak
dibuka untuk placeholder/`<2` pilihan (spy `run_menu`); `/effort` self-interactive;
provider merekam `length` maupun `stop`; ChatAgent menempel peringatan saat
terpotong dan TIDAK menempel saat selesai.

Suite penuh: **1402 passed / 0 failed**; bandit `-ll` clean.

## 6. Catatan target

Semua verifikasi di atas adalah bug **CLI lokal**; tidak ada satu pun request ke
`pnc.siakadcloud.com` atau host lain yang dilakukan untuk investigasi ini.
Jawaban chat WTH-AI di transkrip (SSO-gated, 0 temuan, tidak mengklaim vuln)
tetap sesuai Evidence Law dan tidak diubah.
