# PRD-001: Product

## Problem Statement

WTH-AI adalah CLI conversational agent otonom untuk authorized security testing. Audit menyeluruh menemukan bahwa model keamanan yang didokumentasikan (`AGENT.md`: Scope Law, Evidence Law/TTST, Permission Law, 7-Question Gate) secara konsisten punya celah implementasi — bukan satu-dua bug terisolasi, tapi pola sistemik di banyak lapisan independen. Produk butuh fondasi arsitektural yang membuat model keamanannya BENAR SECARA STRUKTURAL, bukan cuma benar di dokumentasi.

## Vision

Conversational Autonomous Red Teaming CLI: natural-language interface, context-aware, long-running engagement-aware, realtime tool execution, iterative reasoning, evidence-driven, no hallucinated findings, permission-aware, scope-aware, multi-provider LLM, resumable session, specialized internal workflow, observable autonomous loop, explainable actions.

## Personas

1. **Bug bounty hunter individu** — operator tunggal, menjalankan CLI terhadap target program yang mereka ikuti, butuh kecepatan + evidence kuat untuk laporan.
2. **Pentest consultant** — menjalankan engagement client dengan Rules of Engagement formal, butuh audit trail untuk deliverable.
3. **Lab/CTF user** — environment yang mereka miliki penuh, butuh mode paling permisif TAPI tetap dengan visibility (bukan blind spot).
4. **Operator tim** (via bot Discord/Telegram) — mengontrol engagement dari kanal chat, harus tidak bisa lebih permisif dari yang di-cap eksplisit.

## Design Principles (WAJIB)

LLM PROPOSES, POLICY DECIDES, EXECUTOR EXECUTES, EVIDENCE PROVES. Tidak ada satu boolean global yang mematikan >1 dimensi kontrol. Fail-closed default. State machine ditegakkan di titik konsumsi.

## Non-Goals

- Bukan platform hosted multi-tenant (threat model saat ini: single-operator, lihat ADR-013).
- Bukan dashboard web (tetap CLI/TUI/bot).
- Tidak membangun task DAG penuh, model-routing otomatis, atau container sandbox (semua ditolak eksplisit, lihat ADR-002/006/013) — kecuali roadmap produk berubah signifikan (lihat Open Questions di master architecture doc).

## Functional Requirements (ringkasan lintas-PRD)

Lihat PRD masing-masing untuk detail. Ringkasan: Engagement lifecycle (PRD-003/004), Permission granular (PRD-005), Approval terikat aksi (PRD-006), Agent runtime ReAct dengan budget (PRD-007/008), Execution broker tunggal (PRD-011), Evidence-grounded claim (PRD-012), Model abstraction kanonik (PRD-013).

## Non-Functional Requirements

- **Security**: 14 invariant (`WTH-AI-MASTER-ARCHITECTURE.md` §26) harus dipertahankan oleh SETIAP perubahan berikutnya — dicek via test matrix, bukan review manual saja.
- **Reliability**: tidak ada proses subprocess yang leak, tidak ada job worker yang stuck karena timeout kosmetik.
- **Migrasi inkremental**: 334 test existing harus tetap hijau di setiap tahap P0/P1 (tidak ada big-bang rewrite).
- **Auditability**: setiap keputusan policy/approval/eksekusi tercatat durable, tamper-evident.

## Acceptance Criteria (produk secara keseluruhan)

- Deployment default (Docker/`.env.example`) TIDAK lagi punya mode "semua kontrol mati" sebagai default.
- Setiap 14 security invariant punya minimal 1 test otomatis yang akan gagal kalau invariant dilanggar.
- Approval untuk command destruktif tidak bisa "menular" ke command lain yang belum pernah dilihat user.
