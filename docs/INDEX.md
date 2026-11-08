# Indeks dokumentasi

Mulai dari sini; tiap dokumen punya satu tugas dan tidak mengulang yang lain.

| Dokumen | Untuk siapa | Isi |
|---|---|---|
| [`../README.md`](../README.md) | pengguna baru | gambaran produk, instalasi, referensi cepat, struktur proyek |
| [`../AGENTS.md`](../AGENTS.md) | agen & kontributor | konvensi kerja, invarian, alur kerja agen, larangan |
| [`../CLAUDE.md`](../CLAUDE.md) | Claude Code | pointer ringkas ke AGENTS.md |
| [`../llms.txt`](../llms.txt) | agen/otomasi | ringkasan mesin-baca: perintah, artefak, exit code |
| [`EVIDENCE.md`](EVIDENCE.md) | auditor | cara membaca & memverifikasi bundel bukti |
| [`DEMO.md`](DEMO.md) | presenter | urutan demo 5 menit, offline & deterministik |
| [`MODES.md`](MODES.md) | pengguna TUI | matriks mode × kapabilitas (di-generate dari `wth_ai/modes.py`) |
| [`COMPARISON.md`](COMPARISON.md) | penilai | perbandingan dengan Strix, hanya baris yang punya bukti |
| [`AIRGAP.md`](AIRGAP.md) | operator lapangan | pemasangan tertutup, LLM lokal, KB offline, sandbox |
| [`DOCKER.md`](DOCKER.md) | operator | image & container compose (service, volume, env) |
| [`attack/`](attack/destructive-library.md) | pengguna | pustaka payload destruktif (di-generate dari `wth_ai/hunt/payloads.py`) |
| [`../PLAN.md`](../PLAN.md) | kontributor | catatan keputusan & status tiap perubahan yang mendarat |
| [`archive/`](archive/README.md) | — | arsip perencanaan lama, bukan rujukan kerja |
| [`architecture/`](architecture/) | kontributor | catatan arsitektur & interface |

## Aturan penulisan dokumen

1. Satu dokumen, satu tugas. Kalau butuh menyalin isi dokumen lain, tautkan.
2. Setiap klaim teknis harus punya perintah yang bisa dijalankan untuk
   membuktikannya — kalau tidak ada, tulis sebagai rencana, bukan fakta.
3. Angka (jumlah test, jumlah tool, ukuran) harus datang dari eksekusi terakhir,
   bukan dari ingatan.
4. Kalau dokumen bertentangan dengan kode, kode menang; perbaiki dokumennya di
   commit yang sama.