# Slice boundary — audit 2026-11-06 (cabang `big-dix`)

Sumber: `docs/superpowers/plans/2026-11-07-sisa-audit-2026-11-06.md`.
Basis: `2cc4c7e`. Total cabang: 16 commit, 114 file, +1430/−3214 baris.
Batas review yang dipakai: ~400 baris perubahan per PR.

## Kenapa dipecah

Satu PR 16 commit tidak bisa direview fokus. Tiap slice di bawah bisa mendarat
sendiri, punya test sendiri, dan tidak bergantung pada slice lain.

## Keputusan pemilik (2026-11-07)

- Kontaminasi working tree: **dibiarkan**, hanya didokumentasikan di bawah.
- Strategi delivery: **feature branch chain** (`chain_strategy=feature-branch-chain`).

## Slice

| # | Judul | Commit | Baris | Catatan |
|---|---|---|---|---|
| 1 | rpc + gateway: timeout, buffer, sandbox | 735b776, d46f7ac, 71a434f | 3298 | 3116 baris berasal dari penghapusan pohon legacy `agents/` yang terbawa ke commit pertama — lihat "Kontaminasi" |
| 2 | intel: nuclei, sanitasi NVD, filter versi | 9755e91, 16ad464, 1913e02 | 323 | mandiri |
| 3 | eval + computer-use: tulis atomik, prefix type_text, UID, screenshot | 3bd4f48, d73fe9c, d2b94b7, 08a2ca0 | 200 | mandiri |
| 4 | plugins + jobs + cron + broker | 93fcfda, d59b707, c60b83f, 8f1ac92 | 412 | sedikit di atas budget; bisa dipecah plugins (103) dari sisanya |
| 5 | perbaikan temuan review | 5ac7525 | 266 | bergantung pada slice 1–4 |
| 6 | typing Pyright | 638e893 | 175 | bisa digabung ke slice yang relevan saat rebase |

## Kontaminasi working tree

Working tree masuk sesi ini sudah kotor (166 file modified, 46 untracked).
`git add <path>` menambahkan path itu apa adanya, sehingga perubahan yang sudah
ada sebelumnya ikut masuk ke commit task:

- `735b776`: 72 file legacy terhapus + rename `config/wth_config.yaml`.
- `638e893`: `research/engine.py` (+83), `orchestrator.py` (+46),
  `supervisor_dispatch.py`, `executor.py`, `supervisor.py`.

Perubahan itu bukan bagian plan. Sebelum slice 1 dipublikasikan, putuskan:
biarkan (perubahan ikut ter-commit) atau keluarkan lewat rebase interaktif.

## Chain (cabang lokal, belum di-push)

Tracker: `audit/2026-11-06` (draft, tanpa commit). Tiap anak menargetkan induk
langsungnya; slice = rentang commit kontigu.

```
tracker audit/2026-11-06
  └─ s1 audit/s1-rpc-timeout           735b776  (rpc timeout; +3116/-? termasuk pohon legacy)
      └─ s2 audit/s2-gateway-sandbox   71a434f  (buffer gateway + sandbox rpc, 182)
          └─ s3 audit/s3-intel         1913e02  (nuclei, NVD, filter versi, 323)
              └─ s4 audit/s4-eval-computer-use 08a2ca0 (eval + computer-use, 303)
                  └─ s5 audit/s5-plugins-cron-broker 8f1ac92 (plugins, recall, cron, broker, 309)
                      └─ s6 audit/s6-review-fixes 5ac7525 (temuan review, 266)
                          └─ s7 audit/s7-typing-docs e51de65 (typing + dokumen ini, 175)
```

Setiap slice ≤ ~400 baris kecuali s1, yang membawa penghapusan pohon legacy
bawaan working tree (`size:exception` bila kebijakan repo mengizinkan).

Push/PR belum dilakukan: pemilihan strategi tidak memberi izin push.

## Status verifikasi

`pytest tests -q -p no:cacheprovider --timeout=120` → 1484 passed (132,79s).
