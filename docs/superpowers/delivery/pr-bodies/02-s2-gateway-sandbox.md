# PR 02 — fix(gateway,rpc): batasi buffer dan jalankan kode di sandbox

## Chain Context

- Mulai: `71a434f` di atas `audit/s1-rpc-timeout`.
- Berakhir: slice ini saja; slice berikutnya menargetkan cabang ini.
- Dependensi sebelumnya: `audit/s1-rpc-timeout`.
- Budget review: 182 baris (+174/−8).
- Di luar cakupan: -.

## Dependency

```text
└─ audit/s1-rpc-timeout
  └─ audit/s2-gateway-sandbox 📍
    └─ audit/s3-intel
      └─ audit/s4-eval-computer-use
        └─ audit/s5-plugins-cron-broker
          └─ audit/s6-review-fixes
            └─ audit/s7-typing-docs
```text

## Yang berubah

- `wth_ai/gateway/rpc.py`: `MAX_LINE_BYTES = 4*1024*1024`, balasan error JSON-RPC `-32001` untuk request kebesaran.
- `wth_ai/rpc/execute_server.py`: argv dibangun lewat `SandboxSpec.docker_args`, mount `<tmp>:/work:ro`; tanpa docker → `status="blocked"`.

## Verifikasi

- - `tests/test_gateway_daemon.py`, `tests/test_execute_rpc_policy.py`.
- - Suite penuh: 1484 passed.
