"""CLI sandbox plan dan visual cluster: argv dicetak, tidak ada yang dijalankan."""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(args, cwd, home):
    env = {**os.environ, "WTH_SKIP_SETUP": "1", "WTH_PLAIN": "1",
           "WTH_HOME": str(home)}
    return subprocess.run([sys.executable, str(REPO / "wth-ai-main.py"), *args],
                          cwd=cwd, capture_output=True, text=True, timeout=180,
                          env=env)


def test_sandbox_plan_mounts_workspace_file(tmp_path):
    secret = tmp_path / "notes.txt"
    secret.write_text("isi")
    proc = _run(["sandbox", "plan", "--workspace-file", "notes.txt",
                 "--mount", str(tmp_path) + ":/data:ro", "--", "sh", "-lc", "true"],
                tmp_path, tmp_path / "home")
    assert proc.returncode == 0, proc.stderr
    assert "docker run" in proc.stdout
    assert f"{secret}:/work/notes.txt:ro" in proc.stdout
    assert f"{tmp_path}:/data:ro" in proc.stdout
    assert "--read-only" in proc.stdout
    assert "no-new-privileges" in proc.stdout
    assert "tidak dijalankan" in proc.stdout
    assert "destructive=False" in proc.stdout


def test_sandbox_plan_rejects_missing_file(tmp_path):
    proc = _run(["sandbox", "plan", "--workspace-file", "hilang.txt"],
                tmp_path, tmp_path / "home")
    assert proc.returncode != 0
    assert "tidak ada" in (proc.stdout + proc.stderr)


def test_sandbox_plan_rejects_bad_mount(tmp_path):
    proc = _run(["sandbox", "plan", "--mount", "tanpa-titik-dua"],
                tmp_path, tmp_path / "home")
    assert proc.returncode != 0
    assert "tidak valid" in (proc.stdout + proc.stderr)


def test_sandbox_plan_pins_image(tmp_path):
    digest = "deadbeef" * 8  # 64 hex
    image = f"python@sha256:{digest}"
    proc = _run(["sandbox", "plan", "--image", image], tmp_path,
                tmp_path / "home")
    assert proc.returncode == 0, proc.stderr
    assert image in proc.stdout


def test_sandbox_plan_rejects_invalid_image_digest(tmp_path):
    proc = _run(["sandbox", "plan", "--image", "python@sha256:abc"], tmp_path,
                tmp_path / "home")
    assert proc.returncode != 0
    assert "image digest tidak valid" in (proc.stdout + proc.stderr)


def _png(path: Path, width: int, height: int, value: int) -> Path:
    import struct
    import zlib

    raw = bytearray()
    for _ in range(height):
        raw.append(0)
        raw.extend([value, value, value] * width)

    def chunk(kind, payload):
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(bytes(raw)))
                     + chunk(b"IEND", b""))
    return path


def test_visual_cluster_writes_report(tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    for index in range(3):
        _png(shots / f"login{index}.png", 16, 16, 128)
    _png(shots / "admin.png", 16, 16, 0)
    proc = _run(["visual", "cluster", "shots", "--out", "out/clusters.json"],
                tmp_path, tmp_path / "home")
    assert proc.returncode == 0, proc.stderr
    assert "4 gambar" in proc.stdout
    payload = json.loads((tmp_path / "out" / "clusters.json").read_text())
    assert payload["total"] == 4
    assert payload["largest_cluster"] == 3


def test_visual_cluster_missing_dir(tmp_path):
    proc = _run(["visual", "cluster", "tidak-ada"], tmp_path, tmp_path / "home")
    assert proc.returncode != 0
    assert "tidak ada" in (proc.stdout + proc.stderr)