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
wth-ai verify out/bundle/
# verify: OK — out/bundle
```

Mengubah satu byte membuat verifikasi gagal:

```bash
python - <<'PY'
from pathlib import Path
p = Path("out/bundle/findings.json")
p.write_text(p.read_text().replace("F-001", "F-999"))
PY
wth-ai verify out/bundle/     # exit 1, "berubah: findings.json"
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
hash berikutnya tidak cocok, dan `wth-ai verify` melaporkannya.

## Laporan dari bundel yang sama

```bash
wth-ai report --bundle out/bundle --format html   # statis, tanpa server
wth-ai report --bundle out/bundle --format sarif  # 2.1.0, hanya CONFIRMED
wth-ai report --bundle out/bundle --format json   # findings mentah
wth-ai report --bundle out/bundle --format id     # Bahasa Indonesia + rujukan
wth-ai report --bundle out/bundle --format pdf    # satu file, tanpa dependency
wth-ai disclosure F-001 --bundle out/bundle       # advisory 90 hari
```

Semua format dibaca dari `bundle/findings.json` yang sama, jadi angka di PDF,
ID, dan SARIF tidak bisa berbeda satu sama lain.

## Apa yang tidak ada di bundel

- Request/response mentah berukuran besar: yang disimpan hanya yang dirujuk
  temuan, dan isinya sudah diredaksi (`wth_ai/redaction.py`).
- Video/audio, screenshot (disimpan terpisah di `out/screenshots/`).
- Kunci API atau token: kalau muncul di keluaran tool, nilainya jadi `***`
  sebelum ditulis.