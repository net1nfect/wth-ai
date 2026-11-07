# PR 04 — fix(eval,computer-use): tulis atomik, prefix type_text, UID, screenshot

## Chain Context

- Mulai: `08a2ca0` di atas `audit/s3-intel`.
- Berakhir: slice ini saja; slice berikutnya menargetkan cabang ini.
- Dependensi sebelumnya: `audit/s3-intel`.
- Budget review: 303 baris (+284/−19).
- Di luar cakupan: -.

## Dependency

```text
└─ audit/s1-rpc-timeout
  └─ audit/s2-gateway-sandbox
    └─ audit/s3-intel
      └─ audit/s4-eval-computer-use 📍
        └─ audit/s5-plugins-cron-broker
          └─ audit/s6-review-fixes
            └─ audit/s7-typing-docs
```text

## Yang berubah

- `wth_ai/eval/act.py`, `wth_ai/eval/harness.py`: `_write_payload` memakai `atomic_write_text`.
- `computer_use/session.py`: `type_text` melaporkan `f"{len(text)} karakter diketik"` (bukan 12 karakter pertama).
- Discovery X11 menyaring `/proc` berdasarkan `os.getuid()`.
- Screenshot hanya boleh menulis ke `wth_home()/runtime/screenshots` atau `TMPDIR`.

## Verifikasi

- - `tests/test_permission_computer_use.py`, `tests/test_eval_backfill_real.py`.
- - Suite penuh: 1484 passed.
