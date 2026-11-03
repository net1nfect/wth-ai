# PRD-012: Evidence System (`Claim`)

**Priority**: P1
**ADR terkait**: ADR-007
**Dependencies**: PRD-011

## Problem Statement

3 struktur evidence terpisah tidak bersatu (`Evidence`/`EvidencePipeline`, `EvidenceVault`, `ExecutorTask.evidence_schema`), plus bug linkage: `EvidencePipeline.to_findings_candidates()` (`executor/evidence.py:94-108`) membuang `Evidence.id`, cuma simpan `ev.content` (teks) — linkage Finding↔Evidence untuk jalur ini NOL. `FindingState` (8 nilai) hanya 3 dipakai kode. `ReportBuilderAgent.build()` `return raw` tanpa grounding sama sekali (AGENT-03).

**Temuan kritis second-pass critique**: begitu `ExecutionBroker` (PRD-011) otomatis menyimpan evidence di titik primitif, **7 call-site lama jadi sumber duplikasi** — `ExecutorAgent._execute_with_tools()` + 3 vuln-executor (`request_pairs` relay) mengalir ke 3 titik `evidence_vault.store_from_executor()` (`orchestrator.py:781-784,806-808`, `engagement_loop.py:110-112`). Pasangan request/response yang SAMA akan tersimpan dua kali dengan `EV-xxxx` id berbeda, muncul dobel di PoC export report.

## Goals

`Claim` sebagai layer pemersatu SITASI (BUKAN penggantian 3 struktur existing). `EvidenceVault.store()` HANYA boleh dipanggil dari broker pasca-rollout. `EVD-` prefix pensiun.

## Data Model

Lihat `docs/architecture/DATA-MODEL.md` §5 (termasuk §5.1 sequencing revisi kritis).

## Functional Requirements

### Sequencing (revisi final pasca critique — WAJIB diikuti urutan ini)
1. `ClaimStore.create_claim_from_hypothesis()` dipanggil saat **PLANNING** (PRD-009), bukan post-hoc.
2. `claim_id` masuk `ActionIntent.claim_ids` SEBELUM broker `execute()`.
3. Broker, setelah eksekusi sukses match `evidence_schema`/`claim_ids`: `EvidenceVault.store()` (dengan `correlation_id` field baru) → `ClaimStore.append_evidence(claim_id, evidence_ref)` — **APPEND ke claim existing, TIDAK membuat baru**.
4. `EvidencePipeline` turun peran jadi **FALLBACK ONLY** — menangkap evidence yatim yang tidak terpasang ke claim manapun, **TIDAK LAGI menulis `EvidenceVault` sendiri**, hanya membaca id yang sudah ditulis broker.

### Penghapusan Duplikasi (WAJIB, bagian rollout — bukan opsional)
7 call-site lama (`request_pairs` produsen: `executor.py:145`, `executor/vuln/{idor,ssrf,xss}.py`; konsumen: `orchestrator.py:781-784,806-808`, `engagement_loop.py:110-112`) **DIHAPUS** sebagai bagian WU yang sama dengan aktivasi broker auto-store — dicatat eksplisit di `MIGRATION-MATRIX.md`, bukan trade-off tersirat.

### `Claim` Schema
`claim_id, text, claim_type, finding_id, task_id, evidence_refs (["TX:xxxx","EV:xxxx"] — HANYA prefix ini valid, EVD- pensiun), confidence, verification_status (REUSE FindingState), source (provenance), captured_via ("broker_execution:<id>" | "llm_assertion")`.

### Grounding
`GroundingFirewall.validate_claim(claim: Claim)` — method baru, structured (cek `evidence_refs` bukan regex-scan teks), DI SAMPING `validate_claims()` teks-bebas existing (masih perlu untuk narrative report/chat). `ReportBuilderAgent.build()` — validasi setiap finding punya minimal 1 `Claim` dengan `verification_status=VERIFIED` + `evidence_refs` terisi SEBELUM tulis laporan final (menutup AGENT-03).

### Transition Guard
`FindingsDB.update_finding_state()` (tanpa guard hari ini) — tambah transition table (pola sama `LoopStateMachine.TRANSITIONS`), melindungi `Finding.state` DAN `Claim.verification_status` sekaligus. Hanya `VerifierAgent` boleh set `VERIFIED`/`FALSE_POSITIVE`, dengan bobot lebih untuk `captured_via="broker_execution:*"`.

## Acceptance Criteria

- Satu eksekusi broker menghasilkan TEPAT SATU entry `EvidenceVault` (tidak dobel dari jalur lama).
- `Claim.evidence_refs` untuk jalur `EvidencePipeline` terisi id nyata (bug pembuangan `Evidence.id` diperbaiki).
- `ReportBuilderAgent` menolak menulis finding tanpa `Claim` verified ke laporan final.
- `EvidenceVault.store()` dipanggil HANYA dari broker — grep call-site lain menghasilkan nol (setelah 7 titik lama dihapus).

## Out of Scope

Redesign `EvidenceVault` redaction regex coverage (P2, item terpisah `01-gap-analysis.md` #DATA-05).
