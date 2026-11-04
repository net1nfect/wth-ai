# Pemasangan tertutup (air-gap)

WTH-AI tidak butuh telemetri, tidak butuh cloud, dan tidak butuh internet untuk
demo. Yang dibutuhkan hanya Python 3.10+, dan (opsional) Docker untuk
containment.

## 1. Pasang dari sumber

```bash
git clone <repo> wth-ai && cd wth-ai
python3 -m venv .venv && . .venv/bin/activate
pip install -e . --no-index --find-links ./wheelhouse   # atau: pip install -e .
pip install pytest textual
```

Kalau mesin tidak punya internet, siapkan `wheelhouse/` di mesin ber-internet:

```bash
pip download -d wheelhouse -e .
```

## 2. LLM lokal (Ollama)

```bash
export OLLAMA_BASE_URL=http://127.0.0.1:11434
wth model set --role planner --provider ollama --model llama3.1
```

Tidak ada provider cloud yang perlu dikonfigurasi. `WTH_TOKEN_BUDGET` tetap
berlaku, jadi biaya LLM tidak bisa "tak terlihat".

## 3. Containment

```bash
docker network create --internal wth-lab-internal
docker run --rm --network=wth-lab-internal alpine \
  sh -c 'wget -q -T 3 -O- https://1.1.1.1 || echo "EGRESS DENIED (benar)"'
```

Kalau perintah itu mencetak `EGRESS DENIED`, pagar keluar bekerja: network
internal tidak punya gateway, dan `containment.py` melarang published port.

## 4. Demo tanpa jaringan

```bash
wth scan --offline-demo --out out/
wth verify out/bundle/
```

Keduanya tidak menyentuh jaringan sama sekali (fixture deterministik), sehingga
aman direkam di ruang tanpa koneksi.

## 5. KB CVE offline

Basis data KB adalah satu file SQLite. Salin `kb.sqlite` (dari mesin yang punya
internet) ke `$WTH_HOME/kb/kb.sqlite`, lalu:

```bash
wth kb status
wth kb get CVE-2021-44228
```

`wth kb install` (unduh bundel + verifikasi sha256) belum diimplementasikan;
untuk sekarang penyalinan file dilakukan manual, dan laporan mencetak tanggal
`built_at` serta jumlah record yang benar-benar ada — sehingga cakupan KB tidak
pernah diklaim lebih besar dari kenyataan.