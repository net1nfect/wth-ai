# PR 03 — fix(intel): egress template, sanitasi NVD, filter rentang versi

## Chain Context

- Mulai: `1913e02` di atas `audit/s2-gateway-sandbox`.
- Berakhir: slice ini saja; slice berikutnya menargetkan cabang ini.
- Dependensi sebelumnya: `audit/s2-gateway-sandbox`.
- Budget review: 323 baris (+264/−59).
- Di luar cakupan: -.

## Dependency

```text
└─ audit/s1-rpc-timeout
  └─ audit/s2-gateway-sandbox
    └─ audit/s3-intel 📍
      └─ audit/s4-eval-computer-use
        └─ audit/s5-plugins-cron-broker
          └─ audit/s6-review-fixes
            └─ audit/s7-typing-docs
```text

## Yang berubah

- `check_nuclei_templates()` tidak lagi memanggil `subprocess` — selalu `disabled` dengan alasan eksplisit.
- Ringkasan CVE dari NVD dijalankan lewat `sanitize_untrusted_text` dan diberi `provenance="external-nvd"`.
- `_version_in_range(cve, version)` menyaring hint yang tidak berlaku.

## Verifikasi

- - `tests/test_intel_feed.py`.
- - Suite penuh: 1484 passed.
