# 05 — F1 slice 1: `RunningActionRegistry` + kill-switch proses (sisa WU-106)

Status: **SELESAI** (2026-11-04). Kode, wiring, test, dan scan hijau; ter-push.
Rujukan: `docs/planning/02-superiority-plan.md` §F1; `docs/planning/WORK-UNITS.md` WU-106.

## 1. Masalah

WU-106 (parsial) hanya mengimplementasikan interrupt-check in-process
(`ctx.stopped` → loop berhenti di iterasi berikutnya). Proses subprocess yang
SUDAH telanjur berjalan tidak tersentuh — WU-108 mencatat konsekuensinya
eksplisit ("membunuh PROSES nyata butuh RunningActionRegistry, di luar scope
WU ini"): worker timeout meninggalkan proses orphan, dan tidak ada satu pun
cara untuk menghentikan paksa proses yang sedang jalan.

## 2. Yang dibangun

* `wth_ai/running_actions.py` — `RunningActionRegistry`:
  `register(pid, pgid=…, label, actor)`, `unregister`, `snapshot`,
  `active_count`, dan `kill_all(grace_seconds=1.0)` yang mengembalikan laporan
  per-aksi `{terminated, skipped, errors}`. Singleton in-process
  `get_running_actions()` (+ `reset_running_actions()` untuk test).
* `wth_ai/shell_runner.py` — kedua jalur spawn kini memakai
  `start_new_session=True` dan register/unregister ke registry; jalur
  non-streaming dikonversi dari `subprocess.run` ke `Popen` supaya PID
  tertangkap; kill saat timeout menjadi kill **process-tree** (killpg), bukan
  satu PID.
* Dua permukaan kill-switch nyata:
  * `/stop` → `SupervisorAgent.stop()` memanggil `kill_all()` (proses latar
    ikut berhenti, bukan cuma loop).
  * `/quit` dan shutdown lain → `WTHOrchestrator.shutdown()` memanggil
    `kill_all()` **sebelum** `supervisor.stop()`, dan melaporkan jumlahnya di
    pesan penutup ("N proses latar dihentikan").
* **Slice 2 (2026-11-04)**: `wth_ai/recon/runner.py::_run_bounded` (spawn tool
  eksternal) dan `rpc/execute_server.py` (code-exec) kini ikut ter-registrasi —
  `start_new_session=True` + register/unregister + kill seluruh tree saat
  timeout. `kill_process_tree()` diangkat jadi fungsi publik
  `wth_ai/running_actions.py` (dipakai shell_runner + recon + rpc; satu
  implementasi, bukan salinan).
* **Slice 3 (2026-11-04)**: sisa spawn site disapu — `mcp/manager.py`
  (server MCP stdio), `tools/installer.py` (install sampai 600s),
  `intel/feed.py` (nuclei update 120s), `computer_use/session.py`,
  `terminal/backends/docker.py` — semuanya `Popen` + `start_new_session`
  + register/unregister + kill seluruh tree saat timeout.

## 3. Keputusan desain (dan alternatif yang ditolak)

1. **Process GROUP, bukan PID.** `start_new_session=True` membuat anak jadi
   leader group-nya sendiri; satu `killpg` menjangkau anak-cucu. Membunuh PID
   langsung (perilaku lama) meninggalkan cucu orphan — kelas bug yang sama
   dengan pipe-timeout WU-108.
2. **SIGTERM dulu, SIGKILL kemudian.** Beri kesempatan cleanup
   (`grace_seconds`, default 1s), lalu kekerasan berbatas. Fire-and-forget
   tanpa verifikasi ditolak: laporan per-aksi (`terminated/skipped/errors`)
   yang menentukan.
3. **Guard bunuh-diri.** `kill_all` menolak menyentuh process-group proses ini
   sendiri (`os.getpgid(0)`) — salah-registrasi tidak boleh berubah jadi agen
   yang membunuh dirinya sendiri. Entry begini di-`skipped`, tetap terdaftar.
4. **Probe liveness sadar-zombie** (temuan NYATA saat test pertama gagal):
   `killpg(pgid, 0)` tetap "sukses" untuk zombie yang belum di-reap — probe
   naif melaporkan "masih hidup setelah SIGKILL" padahal proses sudah mati.
   Fix: baca state dari `/proc/<pid>/stat` dan abaikan `Z` (fallback killpg
   untuk non-Linux). Pelajaran ini mahal kalau tidak ditemukan test.
5. **Tidak pernah raise.** Dipanggil dari shutdown/interrupt; crash di jalur
   cleanup justru memperburuk. Semua kegagalan jadi entri `errors`.
6. **Entry gagal tidak dihapus diam-diam.** Mati → unregister; gagal dibunuh →
   tetap terdaftar + masuk `errors` supaya operator bisa inspeksi/retry.

## 4. Bukti

`tests/test_running_actions_registry.py` — **14 test**:
lifecycle (register/snapshot/unregister, pgid default, pid invalid ditolak),
kill_all (bunuh proses nyata; jangkau CUCU lewat process-group; guard
own-group; sudah-mati = terminated; registry kosong = no-op),
integrasi shell_runner (register→unregister tanpa leak; pgid==pid terbukti
saat berjalan; timeout tetap 124 + registry bersih; jalur non-streaming),
permukaan kill-switch (`/stop` membunuh proses terdaftar; `shutdown()`
membunuh + melaporkan di pesan).

Slice 2 menambah **3 test** (recon register/clean; recon timeout benar-benar
menghapus process-group; rpc code-exec register/clean). Slice 3 menambah
**5 test** (satu per spawn site baru — proses panjang nyata, dicek
ter-registrasi saat berjalan lalu bersih). Suite penuh:
**1369 passed / 0 failed**; bandit `-ll` clean. Kompatibilitas
lama yang dijaga: `test_worker_and_shell_timeout_reliability` (WU-108),
`test_agent_loop_interrupt` (WU-106 parsial), `test_quit`,
`test_roadmap_platform` — semua hijau.

## 5. Batasan & sisa (jujur — anti-overclaim)

* **Spawn site in-process: SEMUA tersapu** — `shell_runner` (`run_shell`
  agent), `recon/runner` (tools recon eksternal), `rpc/execute_server`
  (code-exec), `mcp/manager` (server MCP), `tools/installer` (install),
  `intel/feed` (nuclei update), `computer_use/session`,
  `terminal/backends/docker`. Tersisa: `tools/pipeline.py` (multi-proses,
  punya `_kill_all` sendiri) — registrasi kecil sebagai follow-up.
* **Flag persisten `runtime/killswitch.json` + entry point CLI/signal
  terpisah** (bisa mematikan walau proses utama stuck) = lingkup F20.
* **`/stop` membunuh SEMUA proses terdaftar** (global), bukan per-task —
  filtering per-engagement menyusul saat binding engagement ada (F1 sisa).
* WU-115 (hapus 7 call-site evidence lama) tetap BLOCKED sampai broker
  auto-evidence — tidak berubah oleh slice ini.

## 6. Peta file

```
wth_ai/running_actions.py                registry + kill_all (baru)
wth_ai/shell_runner.py                   start_new_session + register + kill-tree
wth_ai/recon/runner.py                   idem (tools eksternal)
wth_ai/rpc/execute_server.py             idem (code-exec)
wth_ai/mcp/manager.py                    idem (server MCP; stop() unregister)
wth_ai/tools/installer.py                idem (install sampai 600s)
wth_ai/intel/feed.py                     idem (nuclei update)
wth_ai/computer_use/session.py           idem
wth_ai/terminal/backends/docker.py       idem
wth_ai/agents/supervisor.py              /stop → kill_all
wth_ai/orchestrator.py                   shutdown → kill_all + laporan
tests/test_running_actions_registry.py   22 test
```
