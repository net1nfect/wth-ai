"""Dashboard bundel read-only: loopback, token per proses, GET saja.

Izin berbatas §1.2 poin 1: `wth-ai view` hanya bind 127.0.0.1, hanya membaca
bundel yang sudah ditulis, token acak per proses, tanpa rute mutasi, dan mati
kecuali flag eksplisit. Test ini menjaga keempat batas itu.
"""

from __future__ import annotations

import http.client
import json
import os
import pathlib
import secrets
import subprocess
import sys

import pytest

from wth_ai.view.server import ViewServer, serve

REPO = pathlib.Path(__file__).resolve().parents[1]
#: Token dibuat saat runtime, bukan literal di file. Test ini menguji *syarat*
#: token, bukan nilai tertentu; token literal di repo juga kebiasaan yang tidak
#: perlu dicontoh.
TOKEN = secrets.token_urlsafe(12)

def _bundle(root: pathlib.Path) -> pathlib.Path:
    bundle = root / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(json.dumps({
        "bundle_version": 1,
        "root_hash": "a" * 64,
        "files": ["findings.json"],
    }))
    (bundle / "findings.json").write_text(json.dumps([
        {"id": "F-001", "hypothesis": "BOLA GET /orders/1", "state": "confirmed",
         "severity": "high"},
        {"id": "F-002", "hypothesis": "<script>alert(1)</script>", "state": "candidate",
         "severity": "low"},
    ]))
    (bundle / "root_hash.txt").write_text("a" * 64 + "\n")
    return bundle

def _get(server: ViewServer, path: str, token: str | None = None) -> tuple[int, str]:
    host, port = server.server_address
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {}
    if token is not None:
        headers["X-WTH-Token"] = token
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", errors="replace")
    conn.close()
    return resp.status, body

@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    srv = serve(_bundle(tmp_path), port=0, token=TOKEN)
    try:
        yield srv
    finally:
        srv.stop()

def test_binds_loopback_only(server: ViewServer) -> None:
    host, _ = server.server_address
    assert host == "127.0.0.1"
    assert server.url.startswith("http://127.0.0.1:")

def test_token_is_required(server: ViewServer) -> None:
    assert _get(server, "/")[0] == 403
    assert _get(server, "/", token=TOKEN[:-1] + "x")[0] == 403
    status, body = _get(server, "/", token=TOKEN)
    assert status == 200
    assert "F-001" in body

def test_findings_are_html_escaped(server: ViewServer) -> None:
    status, body = _get(server, "/", token=TOKEN)
    assert status == 200
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body

def test_manifest_and_findings_routes(server: ViewServer) -> None:
    status, body = _get(server, "/manifest", token=TOKEN)
    assert status == 200
    assert json.loads(body)["root_hash"] == "a" * 64

    status, body = _get(server, "/findings", token=TOKEN)
    assert status == 200
    assert [f["id"] for f in json.loads(body)] == ["F-001", "F-002"]

def test_no_mutation_routes(server: ViewServer) -> None:
    host, port = server.server_address
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(method, "/", headers={"X-WTH-Token": TOKEN})
        resp = conn.getresponse()
        assert resp.status in (404, 405), f"{method} harus ditolak"
        resp.read()
        conn.close()

def test_unknown_path_is_404(server: ViewServer) -> None:
    assert _get(server, "/etc/passwd", token=TOKEN)[0] == 404

def test_index_links_carry_the_real_token(server: ViewServer) -> None:
    """Link JSON/manifest di halaman index harus bisa dipakai apa adanya.

    Item 8: halaman menulis `token=TOKEN` sebagai teks harfiah, jadi link
    selalu dijawab 403 walaupun token operator benar.
    """
    status, body = _get(server, "/", token=TOKEN)
    assert status == 200
    assert "token=TOKEN" not in body, "token literal bocor ke HTML"
    assert f"token={TOKEN}" in body

    # Link yang benar-benar ada di halaman harus berfungsi tanpa header token.
    assert _get(server, f"/findings?token={TOKEN}")[0] == 200
    assert _get(server, f"/manifest?token={TOKEN}")[0] == 200

def test_start_is_audited(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    srv = serve(_bundle(tmp_path), port=0, token=TOKEN)
    try:
        audit = (tmp_path / "home" / "logs" / "audit.log").read_text(encoding="utf-8")
    finally:
        srv.stop()
    assert "view_start" in audit

def test_cli_requires_explicit_flag(tmp_path) -> None:
    bundle = _bundle(tmp_path)
    env = {**os.environ, "WTH_HOME": str(tmp_path / "home"),
           "WTH_SKIP_SETUP": "1", "WTH_PLAIN": "1"}
    without = subprocess.run(
        [sys.executable, "wth-ai-main.py", "view", "--bundle", str(bundle)],
        cwd=REPO, capture_output=True, text=True, timeout=120, env=env)
    assert without.returncode != 0
    assert "butuh --yes" in (without.stdout + without.stderr)

    with_flag = subprocess.run(
        [sys.executable, "wth-ai-main.py", "view", "--bundle", str(bundle),
         "--yes", "--print-only"],
        cwd=REPO, capture_output=True, text=True, timeout=120, env=env)
    assert with_flag.returncode == 0, with_flag.stderr
    assert "127.0.0.1" in with_flag.stdout
