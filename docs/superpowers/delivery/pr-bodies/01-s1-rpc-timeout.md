# PR 01 — fix(rpc): validasi timeout eksekusi

## Chain Context

- Mulai: `735b776` di atas `tracker audit/2026-11-06`.
- Berakhir: slice ini saja; slice berikutnya menargetkan cabang ini.
- Dependensi sebelumnya: `tracker audit/2026-11-06`.
- Budget review: 3116 baris (+71/−3045).
- Di luar cakupan: -.

## Dependency

```text
└─ audit/s1-rpc-timeout 📍
  └─ audit/s2-gateway-sandbox
    └─ audit/s3-intel
      └─ audit/s4-eval-computer-use
        └─ audit/s5-plugins-cron-broker
          └─ audit/s6-review-fixes
            └─ audit/s7-typing-docs
```text

## Yang berubah

- `wth_ai/rpc/execute_server.py`: `_parse_timeout(raw, *, default=30, maximum=300)` — nilai timeout dari klien tidak lagi diteruskan mentah ke `subprocess`.

## Verifikasi

- - `tests/test_execute_rpc_policy.py` (timeout default/maksimum/ditolak).
- - Suite penuh: 1484 passed.

## Catatan

- Bawaan working tree yang sudah ada sebelum pekerjaan ini: penghapusan pohon legacy (`agents/`, `core/`, `reconnaissance/`, …) dan rename `config/wth_config.yaml → wth_ai_config.yaml`, ikut di commit ini. Dibiarkan sesuai keputusan pemilik; bukan bagian audit.
