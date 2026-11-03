# PRD Index

> 12 PRD core (full depth) + backlog PRD (structured spec ringkas, ada di `docs/planning/01-gap-analysis.md`) sesuai instruksi "jangan buat dokumen kosong, prioritaskan kualitas". Setiap PRD merujuk `docs/architecture/DATA-MODEL.md` untuk skema, dan `docs/adr/` untuk rasional keputusan besar — tidak diulang di sini.

| PRD | Judul | Priority | Dependencies | Phase |
|---|---|---|---|---|
| [001](PRD-001-product.md) | Product | — | — | Semua |
| [002](PRD-002-cli-experience.md) | CLI Experience | P2 | PRD-007 | P2 |
| [003](PRD-003-engagement-management.md) | Engagement Management | **P0/P1** | — | P0 (FSM+entity), P1 (full store) |
| [004](PRD-004-scope-engine.md) | Scope Engine | **P0/P1** | PRD-003 | P0 (compat shim), P1 (migrasi) |
| [005](PRD-005-permission-policy.md) | Permission & Policy | **P0** | — | P0 |
| [006](PRD-006-approval-system.md) | Approval System | **P0** | PRD-005 | P0 |
| [007](PRD-007-agent-runtime.md) | Agent Runtime | **P0** | — | P0 |
| [008](PRD-008-loop-engine.md) | Loop Engine | **P0/P1** | PRD-007 | P0 (state-machine+fix bug), P1 (budget) |
| [009](PRD-009-planning-task-graph.md) | Planning & Task Representation | P1 | PRD-007, PRD-012 | P1 |
| [010](PRD-010-tool-system.md) | Tool System | **P0** | PRD-005, PRD-006 | P0 |
| [011](PRD-011-execution-broker.md) | Execution Broker | **P0/P1** | PRD-005, PRD-006 | P0 (skeleton+AgentTerminalBridge), P1 (broker penuh) |
| [012](PRD-012-evidence-system.md) | Evidence System (`Claim`) | P1 | PRD-011 | P1 |
| [013](PRD-013-model-provider-abstraction.md) | Model Provider Abstraction | P1 | — | P1 |

**Backlog** (structured spec ringkas, bukan PRD penuh — lihat `docs/planning/01-gap-analysis.md`): Sandbox Runtime, Network Control, Recon Engine Integration, Surface/AssetGraph Knowledge, Hypothesis Engine, Verification Engine, Findings Triage, Model Router (ditolak — ADR-006), Vault/Secrets (KEEP, tidak perlu PRD), Prompt Injection Defense (dicakup PRD-006/011), Event System (dicakup PRD-006/011), Observability, Session/Checkpoint, Reporting, Operator Controls (dicakup PRD-011 kill-switch), Testing/Security Invariants (bagian tiap PRD), Deployment (dicakup Migration Matrix).

## Urutan Baca yang Disarankan

1. PRD-001 (visi) → PRD-003/004 (Engagement/Scope, fondasi otorisasi) → PRD-005/006 (Permission/Approval) → PRD-011 (Broker, titik pertemuan semua) → PRD-007/008 (Agent Runtime/Loop) → PRD-010 (Tool System) → PRD-012 (Evidence/Claim) → PRD-009/013/002 (lapisan atas).
