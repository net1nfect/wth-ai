# 07 — `/effort`: level effort low · medium · high · xhigh · ultra

Status: **SELESAI** (2026-11-04). Kode + 11 test + suite 1394 + bandit hijau; ter-push.
Sumber: keluhan operator — jawaban WTH-AI sering terpotong di tengah kalimat /
terasa terlalu pendek, "kaya ga ada kemampuan buat reason", dan tidak ada cara
menaikkan "kemampuan" dari CLI.

## 1. Akar masalah (bukan model bodoh)

1. **Provider instance di-cache dengan parameter config saat pembuatan**
   (`LLMProviderRouter._get_or_create_provider` → `self._providers[ptype]`).
   Provider dibangun dengan `max_tokens=self.config.models.max_tokens` waktu itu.
   Kalau config diubah belakangan (manual/profile), provider yang sudah di-cache
   **tetap memakai nilai lama** — perubahan tidak pernah berefek. Ini bug nyata.
2. **`config.models.max_tokens` tidak punya permukaan operator.** Nilainya ada
   (default 8192) dan hanya disetel oleh preset `profiles.py`; tidak ada perintah
   untuk melihat/menaikkannya dari sesi.
3. **Jalur render chat tidak memotong apa pun** (dicek: tidak ada slicing
   `response[:N]` di repl/agents/chat) — jadi output pendek berasal dari budget
   generasi / perilaku provider, bukan display kita.
4. Sisa yang **di luar kendali kita**: endpoint provider (custom/nutaraline,
   OpenRouter free tier, dsb.) bisa membatasi output di sisi server, dan model
   reasoning tertentu berhenti lebih awal. Effort tidak menembus pagar provider;
   yang kita kendalikan adalah parameter yang kita kirim.

## 2. Yang dibangun

* `wth_ai/effort.py` — satu sumber kebenaran:
  | level | max_tokens | timeout planner/executor |
  |---|---|---|
  | low | 2048 | 60s / 90s |
  | medium (default lama) | 8192 | 90s / 120s |
  | high | 16384 | 135s / 180s |
  | xhigh | 32768 | 180s / 240s |
  | ultra | 65536 | 270s / 360s |
  `apply_effort()` menulis ke `config.models`; `detect_effort()` menebak level
  aktif (tidak persis cocok → "custom") supaya `/effort` tanpa argumen bisa
  menunjukkan keadaan sekarang.
* `wth_ai/cli/handlers/effort_handler.py` — `handle_effort()`:
  * `/effort` (tanpa argumen): **picker interaktif** (↑/↓, Enter pasang, Esc
    batal) di TTY; di mode plain/gateway menampilkan daftar level + nilai aktif.
  * `/effort <level>`: langsung set → balasan sukses
    `✓ effort → high · max_tokens 16384 · timeout planner 135s / executor 180s · tersimpan, cache provider di-reset`.
  * Level tak dikenal → pesan berisi daftar valid (tidak crash, tidak mengubah apa pun).
* `LLMProviderRouter.reset_provider_cache()` — melepas provider cache supaya
  parameter baru benar-benar dipakai (menutup akar #1). Dipanggil handler.
* Wiring: `SlashRouter` (`/effort`), `PlatformCommand` (kategori `setup`),
  `command_class` (quick command). Ikut otomatis di `/help`, palette `/`,
  menu, dan completer.

## 3. Bukti

`tests/test_effort_command.py` — **11 test**: urutan tier naik ketat & medium ==
default lama (sesi tanpa `/effort` tidak berubah perilaku); apply→detect roundtrip
untuk semua level; level invalid `KeyError` tanpa menyentuh config; handler:
pesan sukses + `max_tokens` berubah + cache reset + **persist nyata ke path tmp**;
level invalid → daftar pilihan; bare `/effort` tanpa tty → daftar + keadaan
sekarang; input case-insensitive; **provider cache: instance lama 8192 → setelah
`high`+reset, instance baru 16384 dan objek berbeda** (bukti akar #1 tertutup);
wiring registry + slash router; end-to-end `WTHOrchestrator.handle_input("/effort ultra")`
→ 65536 + cache kosong.

Catatan keamanan test: fixture menambal `wth_ai.config.USER_CONFIG_PATH`/
`USER_ENV_PATH` ke path tmp — test tidak pernah menulis config asli user.

Suite penuh: **1394 passed / 0 failed**; bandit `-ll` clean; tanpa SyntaxWarning.

## 4. Cara pakai

```
/effort            # picker: low · medium · high · xhigh · ultra (Enter = pasang)
/effort ultra      # langsung, tanpa menu
```

## 5. Sisa (jujur)

* Reasoning-effort passthrough per provider (`reasoning_effort` di payload)
  belum dikirim — endpoint custom bisa menolak parameter tak dikenal; perlu
  flag per-provider + uji terhadap endpoint nyata. Dicatat sebagai kandidat.
* Angka tier (token/timeout) konservatif; kalau provider punya plafon lebih
  tinggi/rendah, tier bisa disetel lewat `EFFORT_LEVELS` (satu tempat).
* Output "pendek" yang berasal dari server provider tidak bisa diperbaiki dari
  sini — `/effort` menaikkan budget kita; sisanya keputusan provider.
