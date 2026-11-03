# Trust Boundaries — Target Architecture

## Diagram

```
                              INTERNET
                                 │
      ┌──────────────────────────┼──────────────────────────┐
      │                          │                          │
 Discord/Telegram          Target eksternal            Provider LLM
 (chat user — TIDAK        (recon/probe/exploit —      (OpenRouter dkk —
  trusted by default)       hasil UNTRUSTED)             trusted transport,
      │                          │                        untrusted CONTENT
      │                          │                        yang dihasilkannya)
      ▼                          ▼                          ▼
┌───────────────────────────────────────────────────────────────────┐
│                         WTH-AI PROCESS                              │
│                                                                       │
│  bots/* ──[FAIL-CLOSED allowlist]──▶ orchestrator                    │
│                                          │                            │
│                                          ▼                            │
│                              agents/* ◄── ModelResponse                │
│                                 │        (berpotensi berisi konten     │
│                                 │         tainted dari target — lihat  │
│                                 │         BrokerContext.tainted)       │
│                                 ▼                                     │
│                        ActionIntent (LLM PROPOSES)                    │
│                                 │                                     │
│                                 ▼                                     │
│  ══════════════ TRUST BOUNDARY UTAMA (POLICY DECIDES) ══════════════  │
│                        ExecutionBroker.execute()                      │
│         Engagement(fetch-fresh) → PolicyEngine(6-layer) →             │
│              ApprovalGate(action-hash) → RunningActionRegistry        │
│  ═══════════════════════════════════════════════════════════════════ │
│                                 │                                     │
│                                 ▼ (EXECUTOR EXECUTES)                 │
│              shell_runner / requests (SATU-SATUNYA sink eksekusi)     │
│                                 │                                     │
│                                 ▼ (EVIDENCE PROVES)                   │
│         EvidenceVault (redaksi) + Claim + audit_log (integrity-chain) │
│                                                                         │
│  vault.py (Fernet, chmod 600) ── kredensial HANYA resolve di titik    │
│                                    eksekusi broker, tidak pernah       │
│                                    plaintext di intent/context/audit   │
└───────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
                    Filesystem lokal (~/.wth-ai/, data/audit.sqlite)
```

## Zona Kepercayaan

| Zona | Trust level | Catatan |
|---|---|---|
| Operator CLI lokal | **Trusted** | Siapa pun dengan akses shell ke mesin — model kepercayaan wajar untuk tool single-operator |
| Bot Discord/Telegram (chat user) | **UNTRUSTED sampai eksplisit allowlist** | Fix NET-01 — default fail-closed, `PermissionProfile` di-cap per sesi bot, tidak pernah mewarisi kapabilitas penuh CLI |
| Target eksternal (recon/probe response) | **SELALU untrusted, structural** | `BrokerContext.tainted` — bukan hanya prompt instruction, tapi hard-force eskalasi approval di Policy Layer 6 |
| Provider LLM (transport) | **Trusted** (API key, TLS) | Tapi KONTEN yang dihasilkan model TIDAK inherently trusted — model bisa "tertipu" oleh tainted input, itu sebabnya keputusan tetap di Policy Engine bukan di reasoning LLM |
| RPC/gateway (Unix socket) | **Trusted** (same-uid, chmod 600) | Bukan network-facing — tapi isi payload (`ExecuteRpcServer`) tetap divalidasi penuh via broker (fix NET-02), bukan dipercaya karena transportnya aman |
| MCP client/server | **Server built-in: trusted (stdio, parent-spawned).** Server eksternal: tergantung konfigurasi operator | Hanya 3 tool read-only untuk server built-in |
| Plugin (`.wth/plugins/`) | **UNTRUSTED dari CWD, trusted dari home** | Fix DATA-01 — auto-discovery CWD-relative dihapus, wajib opt-in |

## Prinsip Lintas-Boundary

1. **Tidak ada boundary yang dilewati oleh "kemudahan"** — `WTH_UNRESTRICTED` adalah contoh boundary yang secara sengaja "dilubangi" untuk kemudahan development, lalu bocor ke default produksi. ADR-012 memastikan tidak ada mekanisme serupa di target architecture: setiap dimensi (capability/scope/approval) punya jalur override sendiri yang eksplisit dan teraudit, tidak ada shortcut gabungan.
2. **Downstream tidak boleh trust upstream tanpa verifikasi ulang di titik eksekusi** — `execute_module()` yang cek `PermissionMode` sekali di pintu masuk lalu request nyata terjadi tanpa re-check adalah pola ANTI-boundary (gate di pintu, bukan di titik tembak). `ExecutionBroker` sebagai chokepoint tunggal (ADR-008) menghapus kemungkinan pola ini terulang di jalur baru.
3. **Trust level menurun seiring jarak dari operator manusia** — CLI langsung (trusted) → agent LLM proposal (advisory, tidak trusted untuk keputusan) → konten dari target (untrusted, taint-tracked) → LLM yang sudah "meracik" tainted content (masih untrusted, approval wajib).
