# ADR-004: Memory Architecture — Contract, Bukan Monolith

**Status**: Accepted
**Domain**: AI/Agent Architecture

## Decision

TIDAK membangun satu `MemoryService` monolitik yang menyembunyikan kelima mekanisme memory di belakang satu API seragam. Sebagai gantinya: kelima mekanisme (`ActionMemory`, `EngagementMemory`, `SessionFtsIndex`, `HuntMemoryLearning`, `MdContext`) tetap terpisah, tapi setiap sumber yang layak masuk context mengekspos kontrak tipis bersama `context_block() -> str` + deklarasi `char_budget`. `EngagementMemory` — satu-satunya yang genuinely tumpang tindih dengan komponen lain — di-merge ke `AssetGraph`.

## Context

5 mekanisme, storage dan lifecycle berbeda-beda, TIDAK saling referensi:

| Mekanisme | Scope | Sifat sebenarnya |
|---|---|---|
| `ActionMemory` | Per-sesi | Loop-engine concern (anti-loop `should_block()`), bukan "memory" dalam arti recall |
| `EngagementMemory` | Per-sesi | Docstring "knowledge graph", isi flat list yang **tumpang tindih** dengan `AssetGraph` |
| `SessionFtsIndex` | Lintas-sesi (SQLite FTS5) | Lexical recall, agent-pull-only lewat tool `session_search` |
| `HuntMemoryLearning` | Lintas-sesi (SQLite relasional) | Aggregate statistik tech_stack→success_rate, well-designed untuk tujuannya |
| `MdContext` | Global, human-authored | Trust/edit model berbeda total — manusia yang menulis |

Dua jalur context-builder (`SupervisorAgent._build_context_block()` vs `WTHOrchestrator._assess_loop()`) dipanggil dengan **frekuensi yang sama** (sekali per giliran user, keduanya) tapi kualitas berbeda — bukan trade-off performa yang disengaja, melainkan drift organik dari dibangun di waktu berbeda.

## Alternatives

1. **Satu `MemoryService`** — abstraksi tunggal menyembunyikan kelimanya.
2. **Biarkan terpisah tanpa kontrak apa pun** (status quo).
3. **Terpisah + kontrak tipis bersama** (`context_block`/`char_budget`) + merge yang genuinely tumpang tindih.

## Chosen Approach

Opsi 3.

## Why

Lifecycle kelimanya genuinely berbeda (session-ephemeral vs cross-session-statistik vs cross-session-lexical vs human-authored) — unifikasi paksa (opsi 1) berisiko mereplikasi masalah `SessionState.metadata` yang sudah ada sekarang (catch-all dict tak berskema tempat banyak field ad-hoc ditumpuk tanpa struktur) di layer baru. Tapi opsi 2 (status quo) membiarkan context assembly tidak konsisten (dump penuh tanpa mempertimbangkan budget lintas-sumber — `MEMORY.md`+`USER.md` bisa memakan sampai 16.000 karakter gabungan tanpa relevansi terhadap target saat ini) dan duplikasi data nyata (`EngagementMemory` vs `AssetGraph` — `controllers`/`middleware`/`trust_boundaries` yang seharusnya jadi atribut node graph yang sudah ada).

`wth_ai/context/builder.py::ContextBuilder` baru dipakai KEDUA jalur (chat & `/plan`) dengan `SOURCE_REGISTRY` yang bisa berbeda per jalur (daftar sumber, bukan implementasi berbeda) — perbedaan jadi eksplisit dan disengaja, bukan dua fungsi yang ditulis ulang independen dan diam-diam melenceng.

## Trade-offs

Kontrak (`context_block` signature) butuh disiplin dijaga konsisten tiap kali ada sumber memory baru ditambahkan — risiko drift kecil tapi nonzero, dimitigasi lewat test kontrak sederhana (tiap entry `SOURCE_REGISTRY` punya test bahwa ia mengembalikan `str` dalam `char_budget`-nya).
