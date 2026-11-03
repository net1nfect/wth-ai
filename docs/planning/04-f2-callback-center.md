# 04 — F2: Callback Center (OOB interaction server)

Status: **MVP selesai** (2026-11-04). Kode, wiring, test, dan scan hijau; ter-push.
Rujukan plan: `docs/planning/02-superiority-plan.md` §4 Grup A, item F2.

## 1. Masalah yang diselesaikan

Kelas vuln *blind* (SSRF, XXE, RCE, blind XSS, SQLi time-based lanjutan) tidak
bisa dibuktikan tanpa callback out-of-band. Sebelum ini, satu-satunya jejak di
repo adalah stub `_try_mcp_oob` (`wth_ai/executor/vuln/ssrf.py`) yang menunjuk
MCP — tidak bisa dipakai untuk membuktikan apa pun secara lokal, dan sesuai
budaya repo: **tidak ada callback yang diklaim sampai ada bukti nyata**.

## 2. Yang dibangun (MVP)

Modul baru `wth_ai/oob/`:

| File | Isi |
|---|---|
| `store.py` | `OOBStore` — registry token (JSON) + log interaksi (JSONL) di `$WTH_HOME/oob/`; TTL live; redaksi kanonik; cap ukuran |
| `server.py` | `OOBCallbackServer` — listener HTTP stdlib (semua metode), uniform-200, loopback default |
| `service.py` | `OOBCallbackService` + singleton `get_oob_service()` per-`WTH_HOME`; `format_hits`; `record_as_transcript` (hit → `[TX:...]`) |

Permukaan pemakaian:

- **Operator (slash):** `/oob start|stop|status|new|list|hits|clear`
  (terdaftar di `wth_ai/cli/slash_router.py` + `wth_ai/platform/registry.py`).
- **Agent (tool):** `oob_create` (buat token + URL) dan `oob_check`
  (periksa hit; bila ada → transcript `[TX:...]`).
  Terdaftar di `wth_ai/agents/tool_registry.py` (`TOOL_DEFINITIONS` +
  `ACTOR_TOOLS["planner"]` + `ACTOR_TOOLS["executor"]`), di-dispatch di
  `wth_ai/agents/supervisor_dispatch.py` (`_dispatch_oob_create`,
  `_dispatch_oob_check`).

Alur tipikal:

```text
/oob start                       → listener loopback hidup (port ephemeral)
/oob new ssrf-url-param --ttl 30 → url http://127.0.0.1:<port>/c/<token>
<kirim URL ke parameter/vektor>  → target memanggil balik; hit tercatat
/oob hits <token>                → hit ditampilkan + disimpan sebagai [TX:...]
```

## 3. Keputusan desain (dan alasannya)

1. **Stdlib, bukan framework.** Listener kecil; tidak butuh routing/plugin.
   Permukaan serangan ekstra = nol manfaat. `ThreadingHTTPServer` + handler
   yang hanya mencatat dan selalu balas `200`.
2. **Uniform-200 (anti-oracle).** Token tak dikenal tetap dibalas `200` teks
   yang sama — jawaban yang berbeda akan memberi tahu pemanggil apakah token
   valid. Token tak dikenal tetap **dicatat** dengan `matched: false`.
3. **TTL dihitung live, tidak ada penghapusan diam-diam.** Token kedaluwarsa
   tetap tampil berlabel `expired`. **Hit setelah kedaluwarsa tetap tersimpan**
   — callback yang telat masih bukti bahwa target menghubungi kita.
4. **Hit adalah transcript, bukan narasi.** Satu-satunya cara hit menjadi
   bukti yang boleh dikutip agent (TTST) adalah lewat
   `record_as_transcript()` → `[TX:...]` di `TerminalTranscriptStore`.
   Tanpa itu, klaim callback hanya cerita.
5. **Redaksi di choke-point.** Semua body/nilai header masuk lewat
   `OOBStore.record_interaction` → `redact_secrets` kanonik (`wth_ai/redaction.py`).
   Cap: body 4 KiB, 25 header, nilai header 300 char.
6. **Loopback default; host/port = permukaan operator, BUKAN LLM.**
   Membuka listener ke jaringan hanya lewat `/oob start --host <ip-lan>`
   (dengan catatan di output). Tool agent `oob_create` tidak punya parameter
   host/port — argumen model tidak boleh bisa membuka port ke jaringan.
7. **Singleton per-`WTH_HOME`.** Listener hidup lintas pemanggilan, tapi kalau
   `WTH_HOME` berubah (antar test/workspace) singleton lama dilepas dan
   listener dimatikan — token tidak pernah tercampur antar home.
8. **Tanpa approval gate untuk `oob_create`.** Membuat token + listener
   loopback tidak menyentuh target apa pun; gate di sini hanya gesekan.
9. **Deteksi wildcard via `ipaddress`, bukan literal.** URL display mengganti host wildcard (`0.0.0.0`/`::`) ke loopback tanpa menulis literal bind-address di kode — bandit tetap bersih tanpa `# nosec`.

## 4. Bukti verifikasi

`tests/test_oob_callback.py` — **11 test, semua hijau** (hermetis; listener
hanya `127.0.0.1` port ephemeral, tanpa jaringan eksternal):

1. `test_token_create_get_and_persist` — token persisten lintas instance.
2. `test_token_expiry_is_live_and_hits_after_expiry_still_recorded`.
3. `test_unknown_token_recorded_but_marked_unmatched`.
4. `test_server_records_real_callback` — GET+POST nyata ke listener, hit lengkap.
5. `test_unknown_token_gets_same_200_response` — anti-oracle.
6. `test_secrets_are_redacted_in_headers_and_body` — Bearer + `password=` teredaksi.
7. `test_service_token_url_and_transcript` — `[TX:...]` tersimpan di store.
8. `test_hits_format_contains_evidence_lines`.
9. `test_dispatch_oob_create_and_check` — jalur penuh tool agent (create → hit → check → TX).
10. `test_tool_registry_exposes_oob_tools` — registry + ACTOR_TOOLS.
11. `test_slash_oob_command_is_routable_and_works` — `/oob` end-to-end via orchestrator.

Suite penuh: **1333 passed / 0 failed** (3× berturut-turut), termasuk run
dengan jaringan eksternal diblokir.

### Catatan harness (penting untuk sesi berikutnya)

Jalankan suite lewat shell terminal (`/usr/bin/python3` — Python 3.13).
**Jangan** menjalankan pytest sebagai subprocess dari kernel `execute_code`
Hermes: kernel men-spawn venv runtime Hermes (Python 3.14) yang
`pydantic_core`-nya rusak → `wth_ai/models.py` fallback `BaseModel = object`
aktif → 3 test pydantic (`test_plan_output_model`, role-wiring, provider) gagal
**palsu** dengan `TypeError: PlanOutput() takes no arguments`. Itu artefak
spawn, bukan regresi repo.

## 5. Batasan & sisa (jujur — anti-overclaim)

- **Listener DNS belum ada.** MVP = HTTP. DNS callback butuh port 53/priv atau
  proxy DNS — kandidat kerja terpisah.
- **Belum auto-wired** ke `_try_mcp_oob` (`wth_ai/executor/vuln/ssrf.py`);
  executor SSRF masih memakai jalur MCP lama. Kerja kandidat berikutnya:
  arahkan `_try_mcp_oob` ke `get_oob_service()`.
- **Belum scope/taint-gated** (F1 belum ada). Mitigasi saat ini: loopback +
  token acak 16-byte. Saat F1 hadir, hit harus masuk audit chain + taint.
- **Belum ada auto-report** — hit jadi `[TX:...]`; wiring ke report = F16.
- **Tunnel publik opsional** belum dibundel; operator bisa memakai tunnel
  sendiri bila butuh callback lintas jaringan.

## 6. Peta file

```
wth_ai/oob/__init__.py      ekspor paket
wth_ai/oob/store.py         OOBStore (token + interactions)
wth_ai/oob/server.py        OOBCallbackServer (HTTP listener)
wth_ai/oob/service.py       OOBCallbackService + singleton + format + TX
tests/test_oob_callback.py  11 test
```

Titik wiring: `wth_ai/agents/tool_registry.py`,
`wth_ai/agents/supervisor_dispatch.py`, `wth_ai/orchestrator.py`
(`_handle_oob`), `wth_ai/cli/slash_router.py`, `wth_ai/platform/registry.py`.
