"""Screenshot clustering: PNG dibaca sendiri, hash mengelompokkan halaman seragam."""

import struct
import zlib
from pathlib import Path

import pytest

from wth_ai.recon.visual import (DEFAULT_THRESHOLD, UnsupportedImage, cluster,
                                 dhash, hamming, read_png_gray, scan_directory)


def _png(path: Path, width: int, height: int, pixel, *, color: int = 2,
         depth: int = 8, interlace: int = 0) -> Path:
    """Tulis PNG minimal tanpa dependency. pixel(x, y) -> tuple channel."""
    channels = {0: 1, 2: 3, 6: 4}[color]
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter none
        for x in range(width):
            values = pixel(x, y)
            if channels == 1:
                raw.append(values if isinstance(values, int) else values[0])
            else:
                raw.extend(list(values)[:channels])
    compressed = zlib.compress(bytes(raw))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    data = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth,
                                         color, 0, 0, interlace))
            + chunk(b"IDAT", compressed)
            + chunk(b"IEND", b""))
    path.write_bytes(data)
    return path


def _stripes(width=32, height=32, flip=False):
    def pixel(x, y):
        band = (x // 4) % 2
        value = 255 if (band == 1) != flip else 0
        return (value, value, value)
    return pixel


def _flat(width=32, height=32):
    return lambda x, y: (128, 128, 128)


def test_read_png_gray_rgb(tmp_path):
    path = _png(tmp_path / "a.png", 4, 2, _flat())
    width, height, gray = read_png_gray(path)
    assert (width, height) == (4, 2)
    assert len(gray) == 8
    assert set(gray) == {128}


def test_read_png_rgba_and_grayscale(tmp_path):
    rgba = _png(tmp_path / "b.png", 2, 2, lambda x, y: (10, 20, 30, 255), color=6)
    assert read_png_gray(rgba)[2][0] == 18  # (10*299+20*587+30*114)/1000
    gray = _png(tmp_path / "c.png", 2, 2, lambda x, y: 77, color=0)
    assert set(read_png_gray(gray)[2]) == {77}


def test_identical_images_hash_equal(tmp_path):
    a = _png(tmp_path / "a.png", 32, 32, _stripes())
    b = _png(tmp_path / "b.png", 32, 32, _stripes())
    assert dhash(a) == dhash(b)
    assert hamming(dhash(a), dhash(b)) == 0


def test_different_images_hash_far_apart(tmp_path):
    a = _png(tmp_path / "a.png", 32, 32, _stripes())
    b = _png(tmp_path / "b.png", 32, 32, _stripes(flip=True))
    assert hamming(dhash(a), dhash(b)) > DEFAULT_THRESHOLD


def test_cluster_groups_uniform_pages_and_separates_odd_one(tmp_path):
    for index in range(3):
        _png(tmp_path / f"login{index}.png", 32, 32, _flat())
    _png(tmp_path / "admin.png", 32, 32, _stripes())
    result = scan_directory(tmp_path)
    assert result["total"] == 4
    assert result["largest_cluster"] == 3
    assert len(result["clusters"]) == 2


def test_broken_file_is_skipped_not_fatal(tmp_path):
    bad = tmp_path / "rusak.png"
    bad.write_bytes(b"bukan png sama sekali")
    good = _png(tmp_path / "ok.png", 8, 8, _flat())
    result = cluster([bad, good])
    assert result["total"] == 1
    assert result["skipped"] and result["skipped"][0]["path"].endswith("rusak.png")


def test_unsupported_png_raises(tmp_path):
    deep = _png(tmp_path / "deep.png", 4, 4, _flat(), depth=16)
    joined = tmp_path / "interlaced.png"
    joined.write_bytes(_png(tmp_path / "tmp.png", 4, 4, _flat(), interlace=1).read_bytes())
    for path in (deep, joined):
        with pytest.raises(UnsupportedImage):
            read_png_gray(path)


def test_empty_directory_is_harmless(tmp_path):
    result = scan_directory(tmp_path)
    assert result == {"total": 0, "clusters": [], "largest_cluster": 0,
                      "skipped": [], "threshold": DEFAULT_THRESHOLD}