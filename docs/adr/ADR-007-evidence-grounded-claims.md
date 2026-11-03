# ADR-007: Evidence-Grounded Findings — Skema `Claim`

**Status**: Accepted
**Domain**: AI/Agent Architecture

## Decision

Tambahkan dataclass `Claim` (`claim_id, text, claim_type, finding_id, task_id, evidence_refs:list[str], confidence:float, verification_status:FindingState, source`) sebagai **layer pemersatu sitasi**, BUKAN penggantian 3 struktur evidence yang ada (`Evidence`/`EvidencePipeline`, `EvidenceVault`, `ExecutorTask.evidence_schema`). `verification_status` REUSE enum `FindingState` yang sudah ada (bukan vocabulary baru).

## Context

3 struktur evidence berbeda, tidak bersatu, plus 1 bug linkage: `Evidence`+`EvidencePipeline` (classifier regex/keyword, id prefix `EVD-`, `to_findings_candidates()` **membuang `Evidence.id`**, cuma simpan `ev.content` teks — linkage Finding↔Evidence untuk jalur ini nol); `EvidenceVault` (paling terstruktur, id prefix `EV-` — beda skema dari `EVD-`, redaksi otomatis, `validate_schema()` cuma substring containment); `ExecutorTask.evidence_schema:dict` (kontrak bebas, tidak divalidasi formal).

Sitasi `[TX:...]`/`[EV:...]` SUDAH ada secara informal — `GroundingFirewall` (`agents/grounding.py`) sudah mengenali pola ini di teks bebas, dan `AgentToolLoop` sudah memproduksinya. `FindingState` (8 nilai) hanya 3 dipakai kode (`CANDIDATE`/`VERIFIED`/`FALSE_POSITIVE`) — 5 lainnya (`HYPOTHESIS`/`NEEDS_EVIDENCE`/`ACCEPTED_RISK`/`FIXED`/`RETEST_PASSED`) menganggur.

## Alternatives

1. **Bongkar 3 struktur, ganti satu model evidence tunggal.**
2. **Biarkan 3 struktur terpisah tanpa layer pemersatu** (status quo).
3. **`Claim` sebagai layer pemersatu SITASI** di atas 3 struktur yang tetap hidup masing-masing dengan tugas sempitnya.

## Chosen Approach

Opsi 3.

## Why

3 struktur yang ada masing-masing sudah melakukan tugas sempitnya dengan cukup baik (schema=kontrak ekspektasi, vault=exhibit locker dengan redaksi, pipeline=classifier observasi mentah) — membongkarnya (opsi 1) adalah rewrite besar untuk masalah yang sebenarnya adalah masalah KOORDINASI, bukan desain masing-masing komponen. `Claim.verification_status` bertipe `FindingState` (bukan vocabulary baru) memberi pekerjaan nyata pada 5 nilai enum yang selama ini menganggur — `Claim` lahir `HYPOTHESIS`, naik `NEEDS_EVIDENCE` begitu ada `task_id` terkait dieksekusi, naik `CANDIDATE` begitu `evidence_refs` terisi, dan HANYA `VerifierAgent` yang boleh menaikkannya ke `VERIFIED`/`FALSE_POSITIVE` — closed-loop lifecycle yang sebelumnya tersirat tapi tidak pernah ditegakkan.

`EvidencePipeline.to_findings_candidates()` diubah OUTPUT-nya jadi `list[Claim]` (memperbaiki bug pembuangan id) — logic classifier internalnya TIDAK diubah. Ini memberi jenjang eksplisit: `Claim` (banyak, murah, mudah dibuang) → `Finding` (sedikit, sudah disaring lewat `VerifierAgent`, masuk laporan) — bukan setiap observasi kecil langsung jadi "finding candidate" seperti sekarang.

## Trade-offs

`FindingsDB.update_finding_state()` (tanpa transition guard sekarang) perlu diberi transition table (pola sama seperti `LoopState.TRANSITIONS` di ADR-011) untuk melindungi KEDUA `Finding.state` dan `Claim.verification_status` sekaligus — ini kerja tambahan yang harus dilakukan bersamaan, bukan opsional, supaya `Claim` yang baru tidak mewarisi kelemahan yang sama (state bisa diubah ke apa pun tanpa validasi).
