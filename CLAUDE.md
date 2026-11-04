# CLAUDE.md

Konvensi kerja untuk repo ini ada di **`AGENTS.md`**. Baca file itu dulu; isinya
berlaku untuk semua agen (Claude Code, Codex, Hermes, dan manusia).

Ringkas, kalau kamu hanya sempat membaca satu layar:

- Repo ini CLI red team Python untuk **authorized testing**. Tanpa scope card
  yang valid (`authorized=true` + CIDR/domain) tidak ada aksi.
- Non-destruktif: hanya GET/HEAD, Modbus hanya FC 1–4. Tidak ada brute force,
  bypass OTP/captcha, atau eksfiltrasi.
- `CONFIRMED` hanya lewat gate veracity (`wth_ai/redteam/classify.py`). Tanpa
  bukti request/response + reproduksi + kontrol negatif, status maksimal
  `candidate`.
- Jangan pernah `shell=True`; jangan cetak secret (pakai `wth_ai/redaction.py`).
- Setiap perubahan disertai test yang gagal dulu, tanpa internet.
- Jalankan: `pytest tests -q -p no:cacheprovider --timeout=120`
- Jangan menulis "sudah berfungsi" tanpa output eksekusi.
- Jangan menambah dashboard/server, updater, license, atau auto-fix PR.

Detail arsitektur, daftar perintah, invarian, dan alur kerja agen:
**`AGENTS.md`**. Dokumen produk: `README.md`, `llms.txt`, `docs/`.