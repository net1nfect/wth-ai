# ADR-010: Fail-Closed Default-Deny Policy Engine

**Status**: Accepted
**Domain**: Security/Platform Architecture

## Decision

`evaluate_intent()` hanya bisa mencapai `ALLOW` lewat pencocokan `Capability` eksplisit di layer Tool Policy. **Tidak ada jalur kode manapun yang bisa `return True`/fallthrough implisit.** 6 layer hierarkis (Kill-switch→Global-Safety→Environment→Engagement→Agent-Capability→Tool-Policy→Action-Policy) masing-masing return eksplisit `DENY`/`ALLOW`/`REQUIRE_APPROVAL`/`DEFER` — tidak ada `continue` implisit ke layer berikutnya tanpa keputusan.

## Context

`jobs/policy.py::PolicyEngine.evaluate()` adalah bukti langsung akar masalah: docstring mengklaim *"Non-destructive, safe-by-default. Rejects out-of-policy jobs"*, tapi implementasi nyatanya:
```python
if is_unrestricted(...): return True, ""
if not safe_mode: return True, ""
return True, ""     # SELALU tercapai, safe_mode=True pun tetap True
```
Bug ini bertahan lolos code-review karena "default deny" hanya jadi NIAT programmer, bukan properti struktural fungsi — dan tidak ada test yang gagal karenanya (`grep "PolicyEngine" tests/*.py` = nol hasil).

`PermissionManager.check_command()` (jalur interaktif) dan `jobs/policy.py::PolicyEngine` (jalur job) adalah **dua implementasi paralel** yang sudah terbukti drift kekuatannya — satu punya denylist check, satunya tidak.

## Alternatives

1. **Linting/code-review manual** untuk menjaga "jangan lupa default deny".
2. **Struktur eksplisit** — tiap layer WAJIB return keputusan eksplisit, plus test wajib.

## Chosen Approach

Opsi 2, **plus** satu core `evaluate_intent()` dipakai KEDUA jalur (interaktif via broker, job admission via adapter tipis) — bukan dua engine paralel. `jobs/policy.py::PolicyEngine.evaluate()` menjadi **coarse pre-filter** (gagal cepat + audit trail di waktu pembuatan job), bukan otoritas final — otoritas final tetap satu: `ExecutionBroker`, dipanggil dari mana pun subprocess/HTTP call betul-betul terjadi (termasuk dari dalam handler job).

## Why

Opsi 1 sudah TERBUKTI tidak cukup — bug fallthrough ini lolos review sebelumnya justru karena mengandalkan disiplin manusia semata. Bug kelas ini (fallthrough-allow) hanya benar-benar dicegah kalau tidak ADA jalur kode menuju `ALLOW` selain lewat rule eksplisit — properti struktural, bukan disiplin penulis kode. Job admission BUKAN titik enforcement akhir yang cukup sendirian — job recon pada akhirnya memanggil `ReconEngine` yang meng-invoke tool eksternal via subprocess sama sekali di luar `AgentTerminalBridge` (lihat ADR-008); kalau job-admission dan command-execution punya engine berbeda, ada *seam* di antara keduanya di mana job bisa "diterima" secara kasar lalu handler-nya menjalankan aksi granular yang seharusnya ditolak di level halus.

## Trade-offs

Setiap layer harus eksplisit return, tidak bisa "continue" implisit — verbositas kode bertambah, tapi ini justru tujuannya: **verbositas = auditability**. Test wajib (audit's own P0 recommendation: matrix `permission_mode × unrestricted-dimension × approved` untuk command destruktif representatif) menambah beban maintenance test, diterima sebagai biaya langsung dari lesson learned bug ini.
