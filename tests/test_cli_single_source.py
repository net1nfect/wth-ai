"""Satu sumber CLI: perilaku produk tidak boleh hanya ada di salinan kedua.

Item 1: root `wth-ai-main.py` dulu salinan penuh yang menyimpang dari
`wth_ai/cli/wth-ai-main.py`:
  * root tidak memverifikasi digest image sandbox (`resolve_image_digest`);
  * root memanggil `write_findings(out_dir, findings)` tanpa `chains=`.
Test di sini mengunci keduanya pada jalur yang benar-benar dijalankan produk.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

REPO = Path(__file__).resolve().parents[1]
PACKAGED = REPO / "wth_ai" / "cli" / "wth-ai-main.py"
ROOT = REPO / "wth-ai-main.py"

def _cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "wth-ai-main.py", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

def _env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "WTH_HOME": str(tmp_path / "home"),
        "WTH_SKIP_SETUP": "1",
        "WTH_PLAIN": "1",
    }

def _load_packaged_cli():
    spec = importlib.util.spec_from_file_location("wth_main_single", PACKAGED)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

def test_packaged_source_is_the_only_definition():
    """Root hanya shim: tidak ada `@cli.command` di sana."""
    root_src = ROOT.read_text(encoding="utf-8")
    assert "@cli.command" not in root_src
    assert "runpy.run_path" in root_src
    assert "@cli.command" in PACKAGED.read_text(encoding="utf-8")

def test_sandbox_image_digest_is_verified_on_product_path(monkeypatch, tmp_path):
    """`sandbox run --image` menolak image yang digest-nya tidak terverifikasi."""
    mod = _load_packaged_cli()

    calls: list[str] = []

    def _fake_resolve(image: str):
        calls.append(image)
        return False, ""

    monkeypatch.setattr("wth_ai.containment.resolve_image_digest", _fake_resolve)
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))

    result = CliRunner().invoke(
        mod.cli,
        ["sandbox", "run", "--yes", "--image", "alpine:3.20", "--", "true"],
    )

    assert result.exit_code != 0, result.output
    assert calls == ["alpine:3.20"], f"digest tidak diverifikasi: {calls}"

def test_scan_findings_include_chains(monkeypatch, tmp_path):
    """`write_findings` dipanggil dengan `chains=`, bukan tanpa rantai."""
    mod = _load_packaged_cli()
    import wth_ai.redteam.artifacts_write as aw

    captured: dict[str, object] = {}

    def _spy(out_dir, findings, *, chains=None):
        captured["chains"] = chains
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "findings.json").write_text(
            json.dumps([f.to_dict() for f in findings]), encoding="utf-8"
        )

    monkeypatch.setattr(aw, "write_findings", _spy)
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))

    result = CliRunner().invoke(
        mod.cli,
        ["scan", "--offline-demo", "--target", "192.0.2.10",
         "--out", str(tmp_path / "out")],
    )

    assert result.exit_code == 0, result.output
    assert "chains" in captured, "write_findings dipanggil tanpa argumen chains"

def test_root_shim_reports_the_package_version(tmp_path):
    """Shim dan sumber paket melaporkan versi yang sama."""
    from wth_ai import __version__

    proc = _cli(_env(tmp_path), "--version")
    assert proc.returncode == 0, proc.stderr
    assert __version__ in proc.stdout
