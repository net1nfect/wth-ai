# Bundel bukti — cara membaca dan memverifikasi

Setiap scan menulis bundel di `out/bundle/`. Bundel ini bisa diverifikasi ulang
tanpa mempercayai tool: semua hash dihitung dari isi file.

```
out/bundle/
  manifest.json      daftar file + sha256 + root_hash
  audit.jsonl        event ber-rantai (tiap baris menautkan hash sebelumnya)
  findings.json      status + attestation per temuan
  scope_card.json    rekaman izin (target, CIDR, mode, destructive)
  root_hash.txt      satu baris: sha256 dari daftar file di manifest
```

Verifikasi:

```bash
wth verify out/bundle/
# verify: OK — out/bundle
```

Mengubah satu byte membuat verifikasi gagal:

```bash
python - <<'PY'
from pathlib import Path
p = Path("out/bundle/findings.json")
p.write_text(p.read_text().replace("F-001", "F-999"))
PY
wth verify out/bundle/     # exit 1, "berubah: findings.json"
```

## Kenapa CONFIRMED sulit dipalsukan

Status temuan tidak diambil dari narasi model. `wth_ai/redteam/classify.py`
memanggil `veracity.assess_claim()` yang mensyaratkan **request/response
tertangkap**, **minimal satu reproduksi**, dan **kontrol negatif yang
berperilaku berbeda**. Tanpa ketiganya, status maksimal `candidate` — dan
`candidate` tidak masuk `findings.sarif` maupun klaim laporan.

## Rantai audit

```python
prev = "0" * 64
for event in events:
    digest = sha256(prev + json.dumps(event, sort_keys=True))
    write({"prev_hash": prev, "hash": digest, "event": event})
    prev = digest
```

Baris pertama selalu `prev_hash = 64 nol`. Memutus rantai di tengah membuat
hash berikutnya tidak cocok, dan `wth verify` melaporkannya.