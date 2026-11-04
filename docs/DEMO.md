# Demo deterministik (rekam dalam urutan ini)

Semua perintah offline. Tidak ada jaringan, tidak ada LLM, hasil sama setiap
kali. Kalau satu perintah gagal, jangan rekam.

```bash
# 0. bersih
rm -rf out && mkdir -p out

# 1. scope (menolak tanpa --authorized)
wth scope --target 172.28.0.10 --cidr 172.28.0.0/16 --authorized --mode auto
# scope: 172.28.0.10 · cidr=172.28.0.0/16 · authorized=yes · mode=auto · ...

# 2. scan (fixture deterministik: 3 dicoba, 1 confirmed, 1 candidate, 1 unproven)
wth scan --offline-demo --out out/
# scan: 3 dicoba · 1 confirmed · 1 candidate · 1 unproven · mode=deep
# bundle: out/bundle · root <12 hex>

# 3. bukti bisa diverifikasi
wth verify out/bundle/
# verify: OK — out/bundle

# 4. bukti gagal saat diubah  ← ini yang paling meyakinkan di video
python - <<'PY'
from pathlib import Path
p = Path("out/bundle/findings.json")
p.write_text(p.read_text().replace("F-001", "F-999"))
PY
wth verify out/bundle/        # exit 1 · "berubah: findings.json"

# 5. gate CI
wth scan -n --offline-demo --fail-on high --out out/ci; echo "exit=$?"
# exit=2

# 6. TUI (butuh terminal sungguhan)
wth interactive --redteam --mode plan
#   tab        → mode berganti (header berubah warna)
#   /modes     → matrix kapabilitas
#   /run exploit  → DITOLAK (mode plan tidak punya exploit)
#   /scope 172.28.0.10 --cidr 172.28.0.0/16 → scope_card.json ditulis
#   /egress-test  → EGRESS DENIED
#   ctrl+k     → KILL ≤2 s
```

Demo TUI otomatis (tanpa terminal interaktif, untuk screenshot):

```bash
python -m wth_ai.cli.tui.redteam --demo
```

## Resume dan workspace (sengaja belum dibangun)

Dua fitur Strix ditunda, dan ini penggantinya supaya tidak ada yang mengira
mesinnya ada:

- **Resume percakapan agen** — pakai job system yang sudah ada:
  `wth job list`, lalu `wth job retry <id>` untuk mengulang job dengan konteks
  yang tersimpan. Tidak ada pemulihan riwayat token per token.
- **Workspace tambahan** — `SandboxSpec.extra_mounts` sudah ada di
  `wth_ai/containment.py`, tetapi belum ada CLI-nya. Untuk sekarang tambahkan
  volume di `docker-compose.offline.yaml`.

## Diff-scope (PR) dan spec API

```bash
# hanya file yang berubah terhadap origin/main yang jadi target
wth scan --offline-demo --out out --scope-mode diff --diff-base origin/main

# host dari OpenAPI/Postman masuk scope, endpoint-nya dicatat di scope card
wth scope --target 172.28.0.10 --cidr 172.28.0.0/16 --authorized \
  --spec openapi.json --spec postman.json
```

## Laporan Bahasa Indonesia

```bash
wth report --bundle out/bundle --format id
# out/report-id.md → "Ringkasan Pimpinan" + "Rujukan Regulasi" + disclaimer
```

## KB CVE dari file mentah (tanpa unduh)

```bash
wth kb build --source ~/kb-src        # nvd/*.json, kev.json, epss.csv
wth kb status
wth kb search log4shell               # tidak ada hasil → exit 1, jangan dikutip
```

## OT/ICS, browser, sandbox

```bash
# rencana baca (tanpa koneksi) vs baca nyata (wajib --confirm + --cidr)
wth ot plan --host 172.28.0.10 --unit 2 --block holding:0:4
wth ot read --host 172.28.0.10 --cidr 172.28.0.0/16 --confirm --count 4

# screenshot (butuh `playwright install chromium`; tanpa itu exit 1 + hint)
wth browser capture http://172.28.0.10/ --out shots/
wth visual cluster shots/ --out out/clusters.json

# sandbox: tanpa --yes hanya mencetak argv, dengan --yes menjalankannya
wth sandbox plan --workspace-file openapi.json -- sh -lc 'echo hi'
wth sandbox run --yes --image kalilinux/kali-rolling -- nmap -sT 172.28.0.10
```

## Kalau ditanya "ini nyata atau hardcode?"

Jawab dengan tiga hal:

1. `wth verify out/bundle/` — hash dihitung dari isi, bukan dari tabel demo.
2. `docs/evidence/f1-containment.txt` — output `docker run` yang gagal keluar.
3. `pytest tests -q` — 1000+ test, termasuk test yang memastikan berkas
   dukungan (findings, sarif, run.json) benar-benar ditulis.