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

## Kalau ditanya "ini nyata atau hardcode?"

Jawab dengan tiga hal:

1. `wth verify out/bundle/` — hash dihitung dari isi, bukan dari tabel demo.
2. `docs/evidence/f1-containment.txt` — output `docker run` yang gagal keluar.
3. `pytest tests -q` — 1000+ test, termasuk test yang memastikan berkas
   dukungan (findings, sarif, run.json) benar-benar ditulis.