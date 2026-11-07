# PR 05 — fix(plugins,jobs,cron,broker): permukaan plugin, digest UNTRUSTED, kunci cron, validasi argumen

## Chain Context

- Mulai: `8f1ac92` di atas `audit/s4-eval-computer-use`.
- Berakhir: slice ini saja; slice berikutnya menargetkan cabang ini.
- Dependensi sebelumnya: `audit/s4-eval-computer-use`.
- Budget review: 309 baris (+288/−21).
- Di luar cakupan: -.

## Dependency

```text
└─ audit/s1-rpc-timeout
  └─ audit/s2-gateway-sandbox
    └─ audit/s3-intel
      └─ audit/s4-eval-computer-use
        └─ audit/s5-plugins-cron-broker 📍
          └─ audit/s6-review-fixes
            └─ audit/s7-typing-docs
```text

## Yang berubah

- `PluginContext.build(config, bus)` → hanya `settings` (mode permission + nama provider); `ctx.config` dihapus.
- `JobDigest` menandai header `UNTRUSTED` dan menyaring teks eksternal.
- `CronStore.locked()` (fcntl) membungkus read-modify-write.
- `broker._valid_http_args` / `_valid_shell_args` menolak tipe argumen salah sebelum dispatch.

## Verifikasi

- - `tests/test_plugins_cwd_noautoexec.py`, `tests/test_recall_and_eval.py`, `tests/test_cron_jobs.py`, `tests/test_broker.py`.
- - Suite penuh: 1484 passed.
