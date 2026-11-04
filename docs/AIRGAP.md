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
  sh -c 'getent hosts example.com >/dev/null 2>&1 || echo "DNS TIDAK TERSEDIA (benar)"'
```

Kalau perintah itu mencetak `DNS TIDAK TERSEDIA`, pagar keluar bekerja: network
internal tidak punya gateway.

WTH-AI juga bisa menyiapkan dan menjalankan container itu untukmu:

```bash
wth sandbox plan --mount ~/lab:/work/ro:ro -- sh -lc 'id'   # hanya cetak argv
wth sandbox run  --yes --image kalilinux/kali-rolling -- nmap -sT 172.28.0.10
```

Tanpa `--yes` perintah kedua menolak jalan (exit 2) dan hanya mencetak argv.
Argumen yang selalu ikut: `--cap-drop ALL`, `no-new-privileges`, `--read-only`,
`--pids-limit`, `--memory`, `--cpus`, `--network wth-lab-internal`.

## 4. Demo tanpa jaringan

```bash
wth scan --offline-demo --out out/
wth verify out/bundle/
```

Keduanya tidak menyentuh jaringan sama sekali (fixture deterministik), sehingga
aman direkam di ruang tanpa koneksi.

## 5. KB CVE offline

Basis data KB adalah satu file SQLite. Dua jalur:

```bash
# mesin ber-internet: unduh bundel mentah, lalu bangun di mesin mana pun
wth kb build --source ~/kb-src      # nvd/*.json, kev.json, epss.csv
wth kb status                       # jumlah record + tanggal built_at

# atau salin langsung file yang sudah dibangun
cp kb.sqlite "$WTH_HOME/kb/kb.sqlite"
wth kb get CVE-2021-44228
```

`wth kb build` tidak mengunduh apa pun; unduhan tetap langkah operator. Laporan
selalu mencetak `built_at` dan jumlah record yang benar-benar ada, sehingga
cakupan KB tidak pernah diklaim lebih besar dari kenyataan.

## 6. Yang opsional (tidak menghalangi demo)

- **Playwright** (`playwright install chromium`) hanya untuk `wth browser capture`.
  Tanpa itu perintahnya exit 1 dengan hint, demo lain tetap jalan.
- **Docker** hanya untuk `wth sandbox run`. `wth sandbox plan` tetap berguna
  tanpa Docker karena ia hanya mencetak argv.
- **Proxy** (`WTH_PROXY`) hanya bila kamu ingin memeriksa trafik di Caido/Burp.
  Tanpa env itu, semua request langsung.