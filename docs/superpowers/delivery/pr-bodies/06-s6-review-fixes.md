# PR 06 — fix: tutup temuan review cabang

## Chain Context

- Mulai: `5ac7525` di atas `audit/s5-plugins-cron-broker`.
- Berakhir: slice ini saja; slice berikutnya menargetkan cabang ini.
- Dependensi sebelumnya: `audit/s5-plugins-cron-broker`.
- Budget review: 266 baris (+240/−26).
- Di luar cakupan: -.

## Dependency

```text
└─ audit/s1-rpc-timeout
  └─ audit/s2-gateway-sandbox
    └─ audit/s3-intel
      └─ audit/s4-eval-computer-use
        └─ audit/s5-plugins-cron-broker
          └─ audit/s6-review-fixes 📍
            └─ audit/s7-typing-docs
```text

## Yang berubah

- rpc: payload non-objek tidak mematikan server; container diberi nama dan dihapus saat timeout; `_network_is_internal()` menolak `wth-lab-internal` yang bukan internal.
- cron: semua mutator memegang lock (reentrant per-thread) dan `_save` atomik.
- intel: versi pendek di-zero-pad, versi tak terbaca → `True`, `intel.cve_lookup` default `False`.
- recall: `_safe_int`; `score` disanitasi.
- redaction: karakter C1, zero-width, dan bidi dibuang.
- plugins: kegagalan `register()` dilaporkan ke stderr + `audit.log`; docstring menyatakan plugin adalah kode in-process tepercaya.

## Verifikasi

- - `tests/test_execute_rpc_policy.py`, `tests/test_cron_jobs.py`, `tests/test_intel_feed.py`, `tests/test_recall_and_eval.py`.
- - Suite penuh: 1484 passed.
