"""SAST codebase dengan opengrep di dalam sandbox tanpa jaringan.

Kenapa sandbox dan bukan proses biasa: opengrep mem-parse kode target dengan
parser pihak ketiga, dan parser adalah permukaan serang. Image dijalankan dengan
`--network none`, `--cap-drop ALL`, dan mount read-only, jadi hasil scan tidak
bisa dipakai untuk keluar jaringan atau menulis ke repo target.

Kenapa `SastUnavailable` dan bukan traceback: opengrep adalah binary eksternal.
Kalau docker atau image tidak ada, pengguna harus mendapat satu kalimat yang
bisa ditindaklanjuti, bukan stack trace di tengah scan.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from wth_ai.audit import audit_log
from wth_ai.containment import SandboxSpec

DEFAULT_IMAGE = "opengrep/opengrep:latest"
DEFAULT_RULES_DIR = Path(__file__).resolve().parent / "rules"
_CODE_DIR = "/src"
_RULES_MOUNT = "/rules"


class SastUnavailable(RuntimeError):
    """Opengrep tidak bisa dijalankan (docker/image/keluaran tidak valid)."""


def docker_available() -> bool:
    return shutil.which("docker") is not None


def run_opengrep(
    target: Path,
    *,
    rules_dir: Path | None = None,
    timeout_s: int = 300,
    image: str | None = None,
) -> list[dict[str, Any]]:
    """Scan `target` dan kembalikan temuan ternormalisasi (masih mentah)."""
    if not docker_available():
        raise SastUnavailable(
            "opengrep tidak tersedia: docker tidak ada di PATH"
        )

    target_path = Path(target).resolve()
    if not target_path.is_dir():
        raise SastUnavailable(f"target bukan direktori: {target_path}")
    rules_path = Path(rules_dir or DEFAULT_RULES_DIR).resolve()
    if not rules_path.is_dir():
        raise SastUnavailable(f"direktori rules tidak ada: {rules_path}")

    spec = SandboxSpec(
        image=image or DEFAULT_IMAGE,
        network="none",
        extra_mounts=(
            f"{target_path}:{_CODE_DIR}:ro",
            f"{rules_path}:{_RULES_MOUNT}:ro",
        ),
    )
    argv = spec.docker_args(
        ["opengrep", "scan", "--json", "--config", _RULES_MOUNT, _CODE_DIR]
    )

    audit_log("sast_scan_start", target=str(target_path), image=spec.image)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        audit_log("sast_scan_timeout", target=str(target_path), timeout=timeout_s)
        raise SastUnavailable(f"opengrep timeout setelah {timeout_s}s") from exc

    # opengrep keluar 0 (bersih) atau 1 (ada temuan); selain itu kegagalan nyata.
    if proc.returncode not in (0, 1):
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise SastUnavailable(f"opengrep gagal (exit {proc.returncode}): {detail}")

    findings = _parse_results(proc.stdout)
    audit_log("sast_scan_done", target=str(target_path), findings=len(findings))
    return findings


def _parse_results(stdout: str) -> list[dict[str, Any]]:
    raw = (stdout or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SastUnavailable(f"keluaran opengrep bukan JSON: {raw[:200]}") from exc
    if not isinstance(payload, dict):
        raise SastUnavailable("keluaran opengrep bukan objek JSON")

    out: list[dict[str, Any]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        raw_extra = item.get("extra")
        extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
        raw_start = item.get("start")
        start: dict[str, Any] = raw_start if isinstance(raw_start, dict) else {}
        out.append(
            {
                "rule_id": str(item.get("check_id", "")),
                "path": str(item.get("path", "")),
                "line": int(start.get("line", 0) or 0),
                "severity": str(extra.get("severity", "INFO")),
                "message": str(extra.get("message", "")),
                "snippet": str(extra.get("lines", ""))[:400],
            }
        )
    return out
