"""Adapter Playwright: tidak terpasang -> unavailable, terpasang -> PNG nyata."""

import os
import stat
import struct
import zlib
from pathlib import Path

from wth_ai.recon import browser


def _png_bytes(value: int = 120, size: int = 8) -> bytes:
    raw = bytearray()
    for _ in range(size):
        raw.append(0)
        raw.extend([value, value, value] * size)

    def chunk(kind, payload):
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw)))
            + chunk(b"IEND", b""))


def _fake_playwright(tmp_path: Path) -> Path:
    """Binary palsu yang menulis PNG ke argumen terakhir."""
    script = tmp_path / "playwright"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import base64, pathlib, sys\n"
        "target = pathlib.Path(sys.argv[-1])\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "target.write_bytes(base64.b64decode("
        + repr(__import__("base64").b64encode(_png_bytes()).decode())
        + "))\n"
        "print('ok')\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_unavailable_without_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_PLAYWRIGHT_BIN", str(tmp_path / "tidak-ada"))
    assert browser.binary() is None
    info = browser.available()
    assert info["available"] is False
    assert "playwright install" in info["hint"]
    result = browser.capture("http://lab/app", tmp_path / "shots")
    assert result["ok"] is False
    assert "playwright install" in result["error"]


def test_capture_writes_png(tmp_path, monkeypatch):
    script = _fake_playwright(tmp_path)
    monkeypatch.setenv("WTH_PLAYWRIGHT_BIN", str(script))
    assert browser.binary() == str(script)
    result = browser.capture("http://lab/app", tmp_path / "shots", name="app")
    assert result["ok"] is True, result
    path = Path(result["path"])
    assert path.is_file()
    assert path.read_bytes().startswith(b"\x89PNG")
    assert result["bytes"] > 0


def test_capture_reports_failure(tmp_path, monkeypatch):
    failing = tmp_path / "playwright"
    failing.write_text("#!/bin/sh\necho 'browser tidak ada' >&2\nexit 3\n")
    failing.chmod(0o755)
    monkeypatch.setenv("WTH_PLAYWRIGHT_BIN", str(failing))
    result = browser.capture("http://lab/app", tmp_path / "shots")
    assert result["ok"] is False
    assert "browser tidak ada" in result["error"]


def test_capture_filename_is_stable(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_PLAYWRIGHT_BIN", str(_fake_playwright(tmp_path)))
    first = browser.capture("http://lab/app/login?x=1", tmp_path / "shots")
    second = browser.capture("http://lab/app/login?x=1", tmp_path / "shots")
    assert first["path"] == second["path"]