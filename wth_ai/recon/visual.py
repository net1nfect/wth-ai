"""Screenshot clustering tanpa dependency gambar.

Masalahnya: recon visual menghasilkan puluhan tangkapan layar, dan yang menarik
hanya yang berbeda dari mayoritas (halaman login seragam, halaman default
seragam). Untuk itu cukup perceptual hash, bukan pemahaman gambar.

Modul ini membaca PNG sendiri (zlib + rekonstruksi filter) supaya tidak menambah
Pillow ke dependency produksi. Format yang didukung: PNG 8-bit, non-interlaced,
grayscale/RGB/RGBA. Format lain dilewati dengan alasan, bukan crash.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIGNATURE = b"\x89PNG\r\n\x1a\n"
HASH_W = 9
HASH_H = 8
MEAN_BITS = 6               # dipertahankan untuk kompatibilitas konstanta lama
MEAN_BITS_MASK = (1 << MEAN_BITS) - 1
MEAN_TOLERANCE = 24         # selisih kecerahan rata-rata yang masih dianggap sama
DEFAULT_THRESHOLD = 6


class UnsupportedImage(ValueError):
    """PNG tidak bisa dibaca modul ini (bukan PNG, 16-bit, atau interlaced)."""


def _chunks(data: bytes):
    offset = len(SIGNATURE)
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset:offset + 4])
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        yield kind, payload
        offset += 12 + length


def read_png_gray(path: Path) -> tuple[int, int, bytes]:
    """Kembalikan (width, height, luminance 8-bit per piksel)."""
    data = Path(path).read_bytes()
    if not data.startswith(SIGNATURE):
        raise UnsupportedImage("bukan PNG")
    header = None
    idat = bytearray()
    for kind, payload in _chunks(data):
        if kind == b"IHDR":
            header = payload
        elif kind == b"IDAT":
            idat += payload
        elif kind == b"IEND":
            break
    if header is None or len(header) < 13:
        raise UnsupportedImage("IHDR tidak ada")
    width, height, depth, color, _compression, _filter, interlace = struct.unpack(
        ">IIBBBBB", header[:13])
    if depth != 8:
        raise UnsupportedImage(f"bit depth {depth} tidak didukung")
    if interlace != 0:
        raise UnsupportedImage("interlaced PNG tidak didukung")
    channels = {0: 1, 2: 3, 6: 4}.get(color)
    if channels is None:
        raise UnsupportedImage(f"color type {color} tidak didukung")
    if not idat:
        raise UnsupportedImage("IDAT kosong")

    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    if len(raw) < height * (stride + 1):
        raise UnsupportedImage("data piksel tidak lengkap")

    out = bytearray(height * width)
    previous = bytearray(stride)
    pos = 0
    for y in range(height):
        filter_type = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if filter_type == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = previous[i]
                c = previous[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + predictor) & 0xFF
        elif filter_type != 0:
            raise UnsupportedImage(f"filter {filter_type} tidak dikenal")
        for x in range(width):
            base = x * channels
            if channels == 1:
                out[y * width + x] = line[base]
            else:
                r, g, b = line[base], line[base + 1], line[base + 2]
                out[y * width + x] = (r * 299 + g * 587 + b * 114) // 1000
        previous = line
    return width, height, bytes(out)


def _sample(gray: bytes, width: int, height: int, x: int, y: int) -> int:
    px = min(width - 1, max(0, (x * width) // HASH_W))
    py = min(height - 1, max(0, (y * height) // HASH_H))
    return gray[py * width + px]


def _mean_bucket(gray: bytes) -> int:
    """Kecerahan rata-rata (0..255).

    Dibandingkan terpisah dari hash: dua gambar rata (halaman putih kosong vs
    halaman hitam) menghasilkan difference-hash yang identik karena tidak ada
    piksel kiri>kanan, tetapi jelas bukan halaman yang sama.
    """
    return (sum(gray) // len(gray)) if gray else 0


def dhash_from_gray(gray: bytes, width: int, height: int) -> int:
    """Difference hash 64-bit: bit = piksel kiri > piksel kanan."""
    value = 0
    for y in range(HASH_H):
        for x in range(HASH_W - 1):
            left = _sample(gray, width, height, x, y)
            right = _sample(gray, width, height, x + 1, y)
            value = (value << 1) | (1 if left > right else 0)
    return value


def fingerprint(path: Path) -> tuple[int, int]:
    """(difference hash, kecerahan rata-rata 0..255).

    Kecerahan dibandingkan terpisah dari hash: dua halaman rata (putih kosong
    dan hitam) menghasilkan hash yang sama karena tidak ada piksel kiri>kanan,
    tetapi jelas bukan halaman yang sama.
    """
    width, height, gray = read_png_gray(path)
    return dhash_from_gray(gray, width, height), _mean_bucket(gray)


def dhash(path: Path) -> int:
    return fingerprint(path)[0]


def hamming(left: int, right: int) -> int:
    return bin(left ^ right).count("1")


def cluster(paths: list[Path], threshold: int = DEFAULT_THRESHOLD,
            mean_tolerance: int = MEAN_TOLERANCE) -> dict:
    """Kelompokkan gambar yang hash-nya berdekatan. Hash gagal -> dicatat terpisah."""
    prints: dict[str, tuple[int, int]] = {}
    skipped: list[dict[str, str]] = []
    for path in paths:
        try:
            prints[str(path)] = fingerprint(path)
        except (UnsupportedImage, OSError, zlib.error) as exc:
            skipped.append({"path": str(path), "reason": str(exc)})

    groups: list[list[str]] = []
    for name, (value, mean) in prints.items():
        for group in groups:
            ref_value, ref_mean = prints[group[0]]
            if (hamming(value, ref_value) <= threshold
                    and abs(mean - ref_mean) <= mean_tolerance):
                group.append(name)
                break
        else:
            groups.append([name])

    groups.sort(key=len, reverse=True)
    return {
        "total": len(prints),
        "clusters": [{"size": len(group), "members": group} for group in groups],
        "largest_cluster": max((len(group) for group in groups), default=0),
        "skipped": skipped,
        "threshold": threshold,
    }


def scan_directory(directory: Path, threshold: int = DEFAULT_THRESHOLD) -> dict:
    directory = Path(directory)
    paths = sorted(p for p in directory.rglob("*.png") if p.is_file())
    return cluster(paths, threshold=threshold)