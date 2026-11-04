# Mode agen WTH-AI

`auto` adalah himpunan penuh kapabilitas. Mode lain adalah pecahan dengan
otonomi lebih rendah; tidak ada mode yang punya kapabilitas di luar `auto`.

| mode | otonomi | tanya | recon | probe | exploit | chain | novel | write | skill | mcp | mutate |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ◇ plan | L0 | ya | ● | ● | · | · | · | ● | ● | ● | · |
| ◆ build | L1 | ya | ● | ● | ● | · | · | ● | ● | ● | · |
| ⚡ auto | L3 | tidak | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| ◎ scout | L2 | tidak | ● | · | · | · | · | · | ● | ● | · |
| ✦ strike | L2 | tidak | · | ● | ● | · | · | · | ● | ● | · |
| ⛓ chain | L2 | tidak | · | · | ● | ● | ● | · | ● | ● | · |
| ✎ scribe | L1 | tidak | · | · | · | · | · | ● | · | ● | · |

## Dua invarian

1. **Scope ditegakkan mesin** — `ScopeManager` + `wth_ai/netguard.py`; mode
   tidak punya flag untuk mematikannya.
2. **Kill switch ≤2 detik** — `ctrl+k` di TUI, `wth kill` untuk worker lain.

## MUTATE

Aksi yang mengubah state target tetap butuh `--destructive` di level proses,
dan flag itu tercatat di `scope_card.json`.
