# Pustaka payload destruktif (berbatas)

Dokumen ini digenerate dari `wth_ai/hunt/payloads.py` (`wth payloads doc`), jadi
isinya tidak bisa menyimpang dari payload yang benar-benar ada di kode.

## Cara kerja batasnya

| blast_radius | arti | boleh dijalankan otomatis |
|---|---|---|
| `self` | hanya objek/marker buatan kita | ya |
| `single-object` | satu objek target, id dicatat untuk pemulihan | ya (bila diizinkan) |
| `mass` | banyak objek/data | **tidak pernah** (`auto_runnable=False`) |

Payload destruktif (`requires_destructive`) hanya jalan bila engagement membuka
`destructive_allowed` (`/scope set destructive=yes`), dan setiap aksi destructif
masuk audit log. Default: tertutup.

## Mengapa destructive dibuat berbatas

Bukan sensor: destruksi tak berbatas menghancurkan bukti yang dibutuhkan laporan,
merusak lingkungan engagement, dan sering menghapus justru data yang membuktikan
bug-nya. Untuk setiap payload massal ada padanan berbatas yang membuktikan kelas
bug yang sama (`bounded_alternative`), dan padanan itu yang dipakai mesin.

## Isi katalog

### api_graphql (5 payload)

#### `API-001` — Introspection schema aktif
- **membuktikan**: Skema penuh dapat dibaca tanpa autentikasi
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `POST {base}/graphql`
- **pemulihan**: Tidak ada perubahan data

#### `API-002` — Aliasing untuk melewati rate limit
- **membuktikan**: Batas laju dihitung per operasi, bukan per kedalaman (alias menggandakan panggilan)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `POST {base}/graphql`
- **pemulihan**: Tidak ada perubahan data

#### `API-003` — Mutasi massal tanpa otorisasi
- **membuktikan**: Mutasi batch tidak memeriksa izin tiap entri
- **blast radius**: `mass` · **reversibel**: tidak · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/graphql`
- **pemulihan**: Jangan dijalankan
- **padanan berbatas**: API-004 (mutasi pada SATU objek milik sendiri) membuktikan izin yang sama tidak diperiksa.

#### `API-004` — Mutasi pada objek milik sendiri lewat id korban
- **membuktikan**: ID pada mutasi tidak divalidasi kepemilikannya
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/graphql`
- **pemulihan**: Kembalikan catatan objek ke nilai semula

#### `API-005` — Kedalaman query berlebih (bounded)
- **membuktikan**: Query bersarang dalam tidak dibatasi (amplifikasi)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `POST {base}/graphql`
- **pemulihan**: Tidak ada perubahan data
- **catatan**: Kedalaman dibatasi 6 — bukan uji DoS.

### auth_session (6 payload)

#### `AU-001` — Token reset dipakai ulang
- **membuktikan**: Token sekali-pakai masih berlaku setelah dipakai
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/reset/confirm`
- **pemulihan**: Setel ulang kata sandi akun uji ke semula

#### `AU-002` — Session fixation pada login
- **membuktikan**: ID sesi tidak dirotasi setelah autentikasi
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/login`
- **pemulihan**: Hapus cookie/sesi uji

#### `AU-003` — JWT alg:none diterima
- **membuktikan**: Verifikasi tanda tangan dapat dilewati dengan alg none
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/api/me`
- **pemulihan**: Tidak ada perubahan data

#### `AU-004` — JWT alg confusion (RS->HS)
- **membuktikan**: Algoritma ditafsirkan dari token, bukan dipatok server
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/api/me`
- **pemulihan**: Tidak ada perubahan data

#### `AU-005` — Rotasi refresh token bocor
- **membuktikan**: Refresh token lama tetap berlaku setelah rotasi
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `POST {base}/api/token/refresh`
- **pemulihan**: Cabut sesi uji

#### `AU-006` — Ganti kata sandi tanpa kata sandi lama
- **membuktikan**: Perubahan kata sandi tidak meminta kredensial saat ini
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/password`
- **pemulihan**: Setel ulang kata sandi akun uji ke semula

### business_logic (7 payload)

#### `BL-001` — Jumlah negatif pada keranjang/order
- **membuktikan**: Validasi kuantitas tidak memeriksa nilai negatif (total bisa jadi negatif atau refund liar)
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/cart`
- **pemulihan**: Hapus baris keranjang yang tercipta (DELETE {base}/api/cart/{own_id}) atau kosongkan keranjang uji
- **catatan**: Gunakan akun uji milik sendiri; jangan selesaikan checkout.

#### `BL-002` — Jumlah nol/pecahan pada endpoint stok
- **membuktikan**: Kuantitas tidak diinteger-kan: nilai 0.0001 atau 0 bisa dipakai membeli tanpa membayar penuh
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/cart`
- **pemulihan**: Hapus baris keranjang uji

#### `BL-003` — Integer overflow pada kuantitas
- **membuktikan**: Kuantitas besar menyebabkan overflow di total/harga (perkalian tak terlindungi)
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/cart`
- **pemulihan**: Hapus baris keranjang uji
- **catatan**: Kirim satu kali; jangan loop nilai.

#### `BL-004` — Manipulasi harga di body checkout
- **membuktikan**: Harga/amount dari klien dipercaya server (price tampering)
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/checkout`
- **pemulihan**: Batalkan order uji, jangan selesaikan pembayaran

#### `BL-005` — Beda mata uang (IDR vs USD) pada transaksi
- **membuktikan**: Mata uang tidak dipatok server: nilai nominal ditafsirkan sebagai mata uang lain
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/checkout`
- **pemulihan**: Batalkan order uji

#### `BL-006` — Pembulatan desimal berulang
- **membuktikan**: Pembulatan menguntungkan klien bila dilakukan berkali-kali (rounding exploit)
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/exchange`
- **pemulihan**: Kembalikan saldo uji bila ada; catat nilai sebelum/sesudah
- **catatan**: Jalankan <=3 kali dan catat saldo; pengulangan besar = mass.

#### `BL-007` — Satuan berbeda pada field yang sama
- **membuktikan**: Server tidak memvalidasi satuan (gram vs kg) sehingga jumlah fisik menyimpang
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/order`
- **pemulihan**: Batalkan order uji

### cache_desync (3 payload)

#### `CD-001` — Cache poisoning lewat header penanda
- **membuktikan**: Respons disimpan tanpa memperhitungkan header input (halaman terkontaminasi)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/`
- **pemulihan**: Minta pembersihan cache atau tunggu TTL; catat URL+header yang tersimpan
- **catatan**: Gunakan header penanda milik sendiri; jangan menyuntik skrip ke pengguna.

#### `CD-002` — Deteksi CL/TE conflict (request smuggling)
- **membuktikan**: Server menerima dua framing berbeda (Content-Length vs Transfer-Encoding)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `POST {base}/`
- **pemulihan**: Tidak ada perubahan data
- **catatan**: Hanya deteksi konflik; tidak memuat request terselundup.

#### `CD-003` — HTTP/2 desync detection
- **membuktikan**: Translasi H2->H1 tidak konsisten (batas permintaan kabur)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `POST {base}/`
- **pemulihan**: Tidak ada perubahan data

### cloud_storage (4 payload)

#### `CL-001` — Bucket dapat ditulis (objek penanda)
- **membuktikan**: Izin tulis publik pada bucket (unauthenticated PUT)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `PUT https://{bucket}.s3.amazonaws.com/{marker}.txt`
- **pemulihan**: Hapus objek {marker}.txt setelah dibuktikan

#### `CL-002` — Pre-signed URL dipakai ulang setelah kedaluwarsa
- **membuktikan**: Kedaluwarsa tanda tangan tidak ditegakkan
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/signed?token={own_id}`
- **pemulihan**: Tidak ada perubahan data

#### `CL-003` — Metadata cloud via SSRF (header saja)
- **membuktikan**: Endpoint internal metadata terjangkau lewat parameter URL
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/fetch?url=http://169.254.169.254/latest/meta-data/`
- **pemulihan**: Tidak ada perubahan data
- **catatan**: Hanya mengambil daftar nama; TIDAK mengambil token kredensial dan tidak memakainya.

#### `CL-004` — Hapus bucket (dokumentasi saja)
- **membuktikan**: Bucket dapat dikosongkan tanpa izin
- **blast radius**: `mass` · **reversibel**: tidak · **butuh opt-in destruktif**: ya
- **probe**: `DELETE https://{bucket}.s3.amazonaws.com/`
- **pemulihan**: JANGAN dijalankan
- **padanan berbatas**: CL-001 (tulis satu objek penanda lalu hapus) sudah membuktikan izin tulis tanpa menghapus data siapa pun.

### file_path (4 payload)

#### `FP-001` — Traversal baca file penanda yang aman
- **membuktikan**: Path traversal memungkinkan pembacaan di luar direktori sajian
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/static/../../etc/hostname`
- **pemulihan**: Tidak ada perubahan data
- **catatan**: Baca berkas penanda (hostname); tidak mengambil data sensitif.

#### `FP-002` — Unggah berkas jinak dengan ekstensi berbahaya
- **membuktikan**: Validasi tipe berkas lemah (diterima berdasarkan ekstensi/nama, bukan isi)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/upload`
- **pemulihan**: Hapus berkas unggahan uji
- **catatan**: Isi berkas hanya teks penanda; tidak ada kode yang dieksekusi.

#### `FP-003` — ZIP-slip dengan path jinak
- **membuktikan**: Arsip diekstrak tanpa normalisasi path (entri keluar direktori)
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/import`
- **pemulihan**: Hapus berkas {marker}.txt di lokasi yang tertulis di respons

#### `FP-004` — Tukar symlink pada berkas sementara
- **membuktikan**: Aplikasi mengikuti symlink saat membaca/menulis berkas sementara
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/export`
- **pemulihan**: Hapus symlink uji

### host_header (3 payload)

#### `HH-001` — Injeksi Host untuk reset kata sandi
- **membuktikan**: Tautan di email memakai Host dari permintaan (reset mengarah ke host penyerang)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/password/forgot`
- **pemulihan**: Abaikan tautan yang terkirim; catat isinya sebagai bukti

#### `HH-002` — CRLF pada header/parameter
- **membuktikan**: Input masuk ke header respons tanpa penyaringan (injeksi header)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/?q={marker}%0d%0aX-Wth-Injected:%20{marker}`
- **pemulihan**: Tidak ada perubahan data

#### `HH-003` — Open redirect lewat parameter
- **membuktikan**: Parameter tujuan diteruskan tanpa daftar izin
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/login?next=https://{marker}.example/`
- **pemulihan**: Tidak ada perubahan data

### idor_write (4 payload)

#### `IW-001` — Ganti nama objek milik user lain (PUT)
- **membuktikan**: Otorisasi tingkat objek tidak diperiksa untuk operasi tulis
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `PUT {base}/api/items/{other_id}`
- **pemulihan**: Kembalikan nama objek ke nilai semula (catat nama SEBELUM mengubah)
- **catatan**: Catat nilai lama dulu; tanpa itu tidak ada pemulihan.

#### `IW-002` — Hapus objek buatan sendiri lewat akun lain
- **membuktikan**: Penghapusan hanya memeriksa autentikasi, bukan kepemilikan (bounded: objek milik kita)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `DELETE {base}/api/items/{own_other_id}`
- **pemulihan**: Buat ulang objek uji setelah dibuktikan

#### `IW-003` — Hapus objek user lain (kontrol)
- **membuktikan**: Sama seperti IW-002 tetapi pada objek korban — HANYA bila target menyediakan akun uji kedua
- **blast radius**: `single-object` · **reversibel**: tidak · **butuh opt-in destruktif**: ya
- **probe**: `DELETE {base}/api/items/{other_id}`
- **pemulihan**: Tidak dapat dipulihkan sendiri — butuh izin eksplisit pemilik data
- **catatan**: Pakai IW-002 dulu; IW-003 hanya bila engagement punya akun uji kedua dan pemulihannya disetujui.

#### `IW-004` — Hapus bersih-bersih (dokumentasi saja)
- **membuktikan**: Penghapusan massal tanpa batas kepemilikan
- **blast radius**: `mass` · **reversibel**: tidak · **butuh opt-in destruktif**: ya
- **probe**: `DELETE {base}/api/items`
- **pemulihan**: Jangan dijalankan
- **padanan berbatas**: IW-002 (hapus objek milik sendiri via akun lain) membuktikan cacat otorisasi yang sama.

### injection_detect (9 payload)

#### `IN-001` — SQLi boolean (perbandingan benar/salah)
- **membuktikan**: Input masuk ke query SQL: hasil berbeda antara kondisi benar dan salah
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/api/items?id=1 AND 1=1`
- **pemulihan**: Tidak ada perubahan data
- **catatan**: Boolean saja. Payload perusak (DROP/DELETE) TIDAK disertakan.

#### `IN-002` — SQLi time-based (SLEEP pendek)
- **membuktikan**: Query dieksekusi di server; jeda terkontrol muncul pada respons
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/api/items?id=1 AND SLEEP(2)`
- **pemulihan**: Tidak ada perubahan data
- **catatan**: Maksimum 3 detik jeda; jangan SLEEP besar (itu DoS).

#### `IN-003` — NoSQL operator injection
- **membuktikan**: Operator objek diteruskan ke query NoSQL (bypass filter)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `POST {base}/api/login`
- **pemulihan**: Tidak ada perubahan data

#### `IN-004` — SSTI aritmetika (7*7)
- **membuktikan**: Ekspresi template dievaluasi server (49 muncul di respons)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/search?q={{7*7}}`
- **pemulihan**: Tidak ada perubahan data
- **catatan**: Bukti aritmetika saja; tidak ada payload eksekusi perintah.

#### `IN-005` — Command injection dengan marker jinak
- **membuktikan**: Argumen diteruskan ke shell (marker unik muncul di keluaran)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/ping?host=127.0.0.1;echo {marker}`
- **pemulihan**: Tidak ada perubahan data
- **catatan**: Hanya `echo`; perintah perusak tidak disertakan.

#### `IN-006` — XXE entitas internal
- **membuktikan**: Parser XML memproses entitas eksternal (kebocoran entitas internal)
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `POST {base}/api/xml`
- **pemulihan**: Tidak ada perubahan data
- **catatan**: Entitas internal saja; tanpa pembacaan file sistem.

#### `IN-007` — LDAP injection boolean
- **membuktikan**: Filter LDAP dapat diubah lewat input
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/directory?user=*)(uid=*))(|(uid=*`
- **pemulihan**: Tidak ada perubahan data

#### `IN-008` — XPath injection boolean
- **membuktikan**: Ekspresi XPath menerima input mentah
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: tidak
- **probe**: `GET {base}/catalog?sku=' or '1'='1`
- **pemulihan**: Tidak ada perubahan data

#### `IN-009` — Injection destruktif (dokumentasi saja)
- **membuktikan**: Data akan hilang bila query destruktif dikirim ke parameter yang sama
- **blast radius**: `mass` · **reversibel**: tidak · **butuh opt-in destruktif**: ya
- **probe**: `GET {base}/api/items?id=1 AND 1=1`
- **pemulihan**: JANGAN dijalankan
- **catatan**: Contoh destruktif (DOKUMENTASI, tidak dibundel sebagai probe): menambahkan komentar query yang menghapus isi tabel pada parameter yang sama. Tidak dijalankan mesin karena bukti kerusakan tidak menambah nilai laporan, sementara datanya hilang nyata.
- **padanan berbatas**: IN-001/IN-002 sudah membuktikan input masuk ke query. Bukti kerusakan tidak menambah nilai laporan dan menghapus data nyata.

### mass_assignment (5 payload)

#### `MA-001` — Tambah atribut is_admin/role di body update profil
- **membuktikan**: Serializer menerima atribut yang tidak seharusnya dapat diubah klien
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `PATCH {base}/api/me`
- **pemulihan**: Kembalikan nama/atribut profil uji ke semula

#### `MA-002` — Ubah balance/credit lewat body
- **membuktikan**: Saldo dapat ditulis dari klien
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `PATCH {base}/api/me`
- **pemulihan**: Setel saldo kembali seperti semula

#### `MA-003` — Pindahkan kepemilikan objek (owner_id)
- **membuktikan**: Kepemilikan objek dapat diubah klien (owner_id/tenant_id mass assignment)
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `PATCH {base}/api/items/{own_id}`
- **pemulihan**: Setel owner_id kembali ke pemilik awal

#### `MA-004` — Lewati verifikasi email/telepon
- **membuktikan**: Flag verified dapat diaktifkan klien tanpa verifikasi
- **blast radius**: `self` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `PATCH {base}/api/me`
- **pemulihan**: Kembalikan flag verifikasi akun uji

#### `MA-005` — Mass assignment pada SEMUA user (dokumentasi saja)
- **membuktikan**: Batch update dapat mengubah atribut seluruh pengguna
- **blast radius**: `mass` · **reversibel**: tidak · **butuh opt-in destruktif**: ya
- **probe**: `PATCH {base}/api/users/bulk`
- **pemulihan**: Jangan dijalankan
- **padanan berbatas**: MA-001 (satu akun uji milik sendiri) sudah membuktikan serializer menerima atribut terlarang.

### race (4 payload)

#### `RC-001` — Klaim kupon/voucher paralel
- **membuktikan**: Check-then-act tanpa kunci: satu kupon terpakai berkali-kali
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/redeem`
- **pemulihan**: Catat kode kupon uji; laporkan pemakaian ganda sebelum menutup engagement

#### `RC-002` — Penarikan saldo paralel
- **membuktikan**: Saldo dapat ditarik melebihi saldo karena saldo dibaca sebelum dikurangi
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/withdraw`
- **pemulihan**: Setor kembali kelebihan pada akun uji; catat saldo sebelum/sesudah
- **catatan**: Hanya akun uji berizin. Jumlahkan selisihnya sebagai bukti, lalu kembalikan.

#### `RC-003` — TOCTOU stok terakhir
- **membuktikan**: Dua pembelian lolos saat stok 1 (inventory oversell)
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/cart/checkout`
- **pemulihan**: Batalkan order uji yang berlebih

#### `RC-004` — Reset token dipakai dua kali secara paralel
- **membuktikan**: Token sekali-pakai tidak atomik: dua permintaan paralel sama-sama diterima
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/reset/confirm`
- **pemulihan**: Setel ulang kata sandi akun uji ke nilai semula

### state_machine (5 payload)

#### `ST-001` — Batalkan setelah dikirim (cancel-after-ship)
- **membuktikan**: Mesin status tidak memeriksa urutan: pembatalan diterima setelah barang dikirim
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/orders/{own_id}/cancel`
- **pemulihan**: Buat ulang order uji bila perlu; catat status awal/akhir

#### `ST-002` — Refund ganda
- **membuktikan**: Refund dapat dipanggil berkali-kali untuk order yang sama
- **blast radius**: `single-object` · **reversibel**: tidak · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/orders/{own_id}/refund`
- **pemulihan**: Tidak dapat dibalik sendiri — laporkan ke pengelola target, jangan ulangi
- **catatan**: Hanya pada akun uji berizin; satu percobaan, lalu stop.

#### `ST-003` — Buka kembali tiket yang sudah ditutup
- **membuktikan**: Status terminal dapat dibalik klien (reopen closed ticket)
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `PATCH {base}/api/tickets/{own_id}`
- **pemulihan**: Set status kembali ke closed

#### `ST-004` — Turunkan paket tanpa pembayaran
- **membuktikan**: Perubahan paket langganan tidak terikat pembayaran (downgrade/upgrade liar)
- **blast radius**: `single-object` · **reversibel**: ya · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/subscription`
- **pemulihan**: Kembalikan ke paket semula

#### `ST-005` — Transisi state massal (dokumentasi saja)
- **membuktikan**: Semua order dapat dipindahkan statusnya sekaligus bila endpoint batch tidak diautorisasi
- **blast radius**: `mass` · **reversibel**: tidak · **butuh opt-in destruktif**: ya
- **probe**: `POST {base}/api/orders/bulk-status`
- **pemulihan**: Tidak dapat dibalik — jangan dijalankan
- **padanan berbatas**: ST-001 (batalkan SATU order uji) membuktikan cacat state machine yang sama tanpa menyentuh data siapa pun.

Total: **59 payload** di **12 kelas** (6 ditandai massal, 37 butuh opt-in destruktif).