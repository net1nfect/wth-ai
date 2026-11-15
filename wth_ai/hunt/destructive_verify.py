"""Verifier jangkar untuk langkah destruktif: bukti bahwa EFEK-nya hilang.

Masalah yang diselesaikan:

Sebelum modul ini, autopilot menandai setiap langkah destruktif yang menerima
respons HTTP sebagai `UNKNOWN` dan tidak pernah mengulanginya. Itu aman, tetapi
terminal: resume berikutnya menahan langkah yang sama selamanya (atau sampai
operator memverifikasi keadaan target secara manual). Status HTTP `200`
membuktikan request selesai, bukan bahwa tulisannya sudah tidak ada.

Kunci desainnya adalah **jangkar (anchor)**, bukan tebak-tebakan:

* Anchor adalah reference request READ-ONLY yang diambil SEBELUM operasi
  destruktif dijalankan. Anchor harus menyebut objek yang akan disentuh
  (mis. `GET /api/cart?item_id=1`), sehingga nilai di dalamnya mewakili
  keadaan *sebelum* — tidak bisa terkontaminasi oleh efek operasi itu sendiri.
* Setelah operasi, reference yang sama dibaca lagi. Operasi destruktif
  dianggap **terverifikasi** hanya bila keadaan sesudah kembali ke keadaan
  jangkar (mis. `qty` -1 hilang, nilai jangkar muncul lagi) — bukan hanya
  karena respons 200.
* Setelah verifikasi, autopilot menjalankan `cleanup` dari katalog: DELETE
  baris yang kita buat, PATCH nilai semula, dan seterusnya. Cleanup baru
  "berhasil" bila reference jangkar setelah cleanup tidak lagi memuat
  indikator efek — kalau masih ada, langkah tetap `UNKNOWN` dengan alasan
  yang bisa ditindaklanjuti.

Verifier ini sengaja deklaratif: ia tidak tahu domain aplikasi. Ia hanya
membandingkan field JSON/teks pada anchor sebelum dan sesudah, dan mencari
indikator efek yang diasosiasikan dengan payload. Payload yang tidak punya
anchor aman tetap `UNKNOWN` — jangan menebak.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

#: Marker yang dipakai katalog payload untuk menandai jejak kita sendiri.
DEFAULT_EFFECT_MARKER = "wth-auto-9z8y7"

#: Nilai probe yang BERARTI "berhasil" bila muncul di keadaan target.
#:
#: Katalog menulis nilai efeknya di `body` payload. Daftar ini adalah
#: indikator POSITIF: bila nilai itu terlihat pada anchor sesudah operasi,
#: berarti efek masih ada. Nilai yang wajar bukan bukti apa pun; hanya nilai
#: yang secara struktural khas payload yang dipakai.
_PAYLOAD_EFFECT_VALUES: tuple[tuple[str, str], ...] = (
    ("BL-001", "-1"),            # qty negatif
    ("BL-002", "0.0001"),        # qty pecahan
    ("BL-003", "2147483648"),    # integer overflow
    ("BL-004", '"price":1'),     # harga dimanipulasi di checkout
    ("ST-003", "closed"),        # status tiket diubah
    ("ST-004", '"plan":"enterprise"'),
)

ProbeRunner = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class EffectAnchor:
    """Reference read-only yang mendefinisikan "keadaan awal".

    `probe` harus berupa request READ-ONLY (GET/HEAD) yang menyebut objek yang
    akan disentuh operasi. `expected` adalah nilai yang WAJIB ada di keadaan
    awal (bila ada): bila nilai itu hilang setelah operasi, berarti objeknya
    sudah berubah/hilang — bukan keadaan yang sama.
    """

    probe: dict[str, Any]
    expected: tuple[str, ...] = ()
    description: str = ""

    @property
    def key(self) -> str:
        return str(self.probe.get("url", ""))


@dataclass
class Verification:
    """Hasil verifikasi satu langkah terhadap anchor."""

    verified: bool
    reason: str
    #: True bila reference sesudah operasi tidak bisa dibaca (request gagal,
    #: status 0, respons kosong). Verifikasi yang tidak mungkin dilakukan
    #: TIDAK boleh menghasilkan `verified=False` yang bisa disalahartikan
    #: sebagai "target bersih"; ia dilaporkan sebagai tidak tersedia.
    unavailable: bool = False
    evidence_refs: list[str] = field(default_factory=list)


def marker_effect(marker: str | None = None) -> str:
    return f"wth-marker:{marker or DEFAULT_EFFECT_MARKER}"


def payload_effect_indicators(payload_id: str) -> tuple[str, ...]:
    """Nilai khas yang menandai efek payload masih terlihat di keadaan target."""
    payload_id = str(payload_id or "").strip().upper()
    out: list[str] = []
    for pid, value in _PAYLOAD_EFFECT_VALUES:
        if pid == payload_id:
            out.append(value)
    return tuple(out)


def _read(probe: Mapping[str, Any], run_probe: ProbeRunner) -> tuple[str, str]:
    """Baca anchor. Return (body, status); body kosong = tidak terbaca."""
    try:
        result = run_probe(dict(probe))
    except Exception:  # noqa: BLE001 - verifikasi gagal != target rusak
        return "", ""
    status = str(getattr(result, "status_code", "") or "")
    body = str(getattr(result, "stdout", "") or "")
    return body, status


def _json_fields(body: str) -> dict[str, str]:
    """Flatten field JSON tingkat atas yang dipakai untuk membandingkan keadaan.

    Hanya string/angka/bool yang diambil; struktur bersarang diabaikan karena
    anchor yang tidak menyebut field spesifik tidak bisa diverifikasi dengan
    jujur. Body non-JSON dikembalikan kosong (indikator teks tetap dipakai).
    """
    text = body.strip()
    if not text or text[0] not in "[{":
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)):
            out[str(key)] = str(value)
    return out


def _contains_indicator(body: str, indicators: Sequence[str]) -> str | None:
    lowered = body.lower()
    for indicator in indicators:
        if indicator and indicator.lower() in lowered:
            return indicator
    return None


def verify_effect(
    *,
    payload_id: str,
    anchor: EffectAnchor | None,
    run_probe: ProbeRunner,
    before_body: str,
    requires_cleanup: bool = False,
) -> Verification:
    """Apakah efek payload sudah TIDAK terlihat pada anchor sesudah operasi?

    `before_body` wajib berasal dari anchor yang diambil SEBELUM operasi —
    caller menyediakannya supaya pembacaan sesudah tidak bisa "membuktikan"
    keadaan yang sudah tercemar oleh operasi itu sendiri.
    """
    if anchor is None:
        return Verification(
            verified=False,
            reason=(
                "tidak ada anchor read-only untuk objek yang disentuh; "
                "efek tidak dapat diverifikasi tanpa membaca keadaan target"
            ),
            unavailable=True,
        )

    after_body, after_status = _read(anchor.probe, run_probe)
    if not after_body and not after_status:
        return Verification(
            verified=False,
            reason=f"anchor {anchor.key} tidak dapat dibaca setelah operasi",
            unavailable=True,
        )

    indicators = payload_effect_indicators(payload_id)
    left = _contains_indicator(after_body, indicators)
    if left:
        return Verification(
            verified=False,
            reason=(
                f"efek payload masih terlihat di {anchor.key} (indikator '{left}')"
            ),
        )

    if requires_cleanup:
        wasted = marker_effect()
        if wasted.split(":", 1)[-1] in after_body.lower():
            return Verification(
                verified=False,
                reason=f"jejak marker uji masih ada di {anchor.key}",
            )

    before_fields = _json_fields(before_body)
    after_fields = _json_fields(after_body)
    if anchor.expected:
        missing = [
            name for name in anchor.expected
            if name not in after_fields and name not in after_body
        ]
        if missing:
            return Verification(
                verified=False,
                reason=(
                    "objek jangkar berbeda dari keadaan awal: field "
                    f"{', '.join(missing)} tidak ada di {anchor.key}"
                ),
            )
    if before_fields and after_fields:
        if before_fields == after_fields:
            return Verification(
                verified=True,
                reason=f"{anchor.key} kembali ke keadaan jangkar",
            )
        changed = sorted(
            key for key in set(before_fields) | set(after_fields)
            if before_fields.get(key) != after_fields.get(key)
        )
        if changed and not left:
            # Field berbeda tapi tidak ada indikator efek payload: tidak bisa
            # dibedakan dari perubahan lain (mis. timestamp). Jangan klaim.
            return Verification(
                verified=False,
                reason=(
                    "keadaan anchor berbeda dari jangkar pada "
                    f"{', '.join(changed)}; bukan bukti pemulihan"
                ),
                unavailable=True,
            )

    if not before_body.strip():
        return Verification(
            verified=False,
            reason=f"jangkar {anchor.key} kosong sebelum operasi; tidak ada pembanding",
            unavailable=True,
        )

    # Tidak ada indikator positif yang tersisa. Untuk payload tanpa indikator
    # terdaftar, "kembali ke keadaan awal" tetap membutuhkan pembanding
    # struktural; bila tidak ada, jangan mengarang verdict.
    if not indicators:
        return Verification(
            verified=False,
            reason=(
                "payload tidak punya indikator efek terdaftar; "
                "keadaan hanya bisa diverifikasi manual"
            ),
            unavailable=True,
        )
    return Verification(
        verified=True,
        reason=f"indikator efek payload tidak lagi ada di {anchor.key}",
    )


def verify_cleanup(
    *,
    payload_id: str,
    anchor: EffectAnchor | None,
    run_probe: ProbeRunner,
) -> Verification:
    """Apakah cleanup berhasil (jejak kita hilang)?

    Dipakai setelah `POST .../cleanup` (DELETE/PATCH) yang sukses. Bila anchor
    tidak terbaca, hasilnya `unavailable` — bukan gagal, bukan sukses.
    """
    if anchor is None:
        return Verification(
            verified=False,
            reason="tidak ada anchor untuk memverifikasi cleanup",
            unavailable=True,
        )
    body, status = _read(anchor.probe, run_probe)
    if not body and not status:
        return Verification(
            verified=False,
            reason=f"anchor {anchor.key} tidak dapat dibaca setelah cleanup",
            unavailable=True,
        )
    indicators = payload_effect_indicators(payload_id)
    left = _contains_indicator(body, indicators)
    if left:
        return Verification(
            verified=False,
            reason=f"cleanup: efek '{left}' masih terlihat di {anchor.key}",
        )
    marker = DEFAULT_EFFECT_MARKER
    if marker in body.lower():
        return Verification(
            verified=False,
            reason=f"cleanup: marker uji masih ada di {anchor.key}",
        )
    return Verification(
        verified=True,
        reason=f"cleanup terverifikasi: {anchor.key} bersih dari efek payload",
    )
