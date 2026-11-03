# Architecture Decision Records — Index

| ADR | Judul | Domain | Status |
|---|---|---|---|
| [001](ADR-001-agent-vs-workflow.md) | Agent-vs-Workflow Architecture | AI/Agent | Accepted |
| [002](ADR-002-task-representation.md) | Task Representation — Flat List vs DAG | AI/Agent | Accepted |
| [003](ADR-003-model-provider-abstraction.md) | Model Provider Abstraction — Canonical Envelope | AI/Agent | Accepted |
| [004](ADR-004-memory-architecture.md) | Memory Architecture — Contract, Bukan Monolith | AI/Agent | Accepted |
| [005](ADR-005-event-driven-vs-polling.md) | Event-Driven vs Polling untuk Agent Reasoning Loop | AI/Agent | Accepted |
| [006](ADR-006-reject-automatic-model-routing.md) | Automatic Model Routing — Ditolak | AI/Agent | Accepted (Rejected proposal) |
| [007](ADR-007-evidence-grounded-claims.md) | Evidence-Grounded Findings — Skema `Claim` | AI/Agent | Accepted |
| [008](ADR-008-execution-broker.md) | Execution Broker sebagai Chokepoint Tunggal | Security/Platform | Accepted |
| [009](ADR-009-capability-based-permissions.md) | Capability-Based Permissions | Security/Platform | Accepted |
| [010](ADR-010-fail-closed-policy-engine.md) | Fail-Closed Default-Deny Policy Engine | Security/Platform | Accepted |
| [011](ADR-011-engagement-lifecycle-fsm.md) | Engagement Lifecycle State Machine | Security/Platform | Accepted |
| [012](ADR-012-split-unrestricted-flag.md) | Memecah `WTH_UNRESTRICTED` jadi Dimensi Independen | Security/Platform | Accepted |
| [013](ADR-013-sandbox-boundary.md) | Sandbox Boundary — OS-Level, Bukan Container | Security/Platform | Accepted |

Catatan: keputusan "hapus kode legacy `core/`/`agents/` root/dll" (item #29 di `docs/planning/01-gap-analysis.md`) sengaja TIDAK mendapat ADR tersendiri — sudah terkonfirmasi dead code tanpa ambiguitas/trade-off yang perlu didiskusikan (lihat `MIGRATION-MATRIX.md`), berbeda dari 13 keputusan di atas yang semuanya punya alternatif nyata yang dipertimbangkan dan ditolak.

Semua ADR disusun oleh 2 spesialis (AI/Agent Architecture; Security Control & Platform Architecture) yang bekerja berurutan dengan riset langsung terhadap source code `wth_ai/`, direview lewat second-pass critique silang, dan direkonsiliasi oleh main agent. Lihat `docs/planning/00-existing-architecture.md` untuk evidence lengkap current-state yang mendasari tiap keputusan.
