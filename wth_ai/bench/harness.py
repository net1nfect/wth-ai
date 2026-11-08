"""Harness benchmark offline: jalankan task terhadap server palsu lokal.

Kenapa server palsu, bukan target nyata: benchmark harus deterministik dan tidak
boleh punya jalur keluar. Setiap task menyajikan respons dari 127.0.0.1 dan
temuan yang dilaporkan hanya yang benar-benar dibuktikan oleh respons itu —
hypothesis yang rutenya mati tidak dihitung sebagai temuan.

Batas kejujurannya: harness ini menguji *pipeline bukti*, bukan kualitas
penalaran agen. Ia tidak memanggil LLM dan tidak boleh dipakai untuk mengklaim
"agen menemukan X di target sungguhan".
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

TASK_FILE = "task.json"
TRUTH_FILE = "ground_truth.json"
DEFAULT_TIMEOUT_S = 5.0

class BenchError(RuntimeError):
    """Task tidak bisa dijalankan (konfigurasi hilang/rusak)."""

@dataclass
class BenchResult:
    task: str
    passed: bool
    findings: list[dict[str, Any]] = field(default_factory=list)
    duration_s: float = 0.0
    ground_truth_hits: int = 0
    truth_total: int = 0
    targets: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def precision(self) -> float:
        if not self.findings:
            return 0.0 if self.truth_total else 1.0
        return self.ground_truth_hits / len(self.findings)

    @property
    def recall(self) -> float:
        if not self.truth_total:
            return 1.0
        return self.ground_truth_hits / self.truth_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "passed": self.passed,
            "findings": self.findings,
            "duration_s": self.duration_s,
            "ground_truth_hits": self.ground_truth_hits,
            "truth_total": self.truth_total,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "targets": self.targets,
            "error": self.error,
        }

def task_dirs(root: Path) -> list[Path]:
    """Semua direktori task di bawah `root`, urut nama supaya deterministik."""
    root = Path(root)
    if not root.is_dir():
        raise BenchError(f"direktori task tidak ditemukan: {root}")
    return sorted(p for p in root.iterdir() if (p / TASK_FILE).is_file())

def _load(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise BenchError(f"{path} tidak ada: {label} wajib ada") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BenchError(f"{path} bukan JSON valid: {exc}") from exc
    if not isinstance(data, dict):
        raise BenchError(f"{path} harus objek JSON")
    return data

def _handler_for(responses: dict[str, dict[str, Any]]) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - nama wajib dari BaseHTTPRequestHandler
            spec = responses.get(self.path)
            if spec is None:
                self._send(404, {"error": "tidak ada rute"})
                return
            self._send(int(spec.get("status", 200)), spec.get("body", ""))

        def _send(self, status: int, body: Any) -> None:
            payload = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            # Benchmark tidak boleh mengotori stdout pytest dengan log per request.
            return

    return _Handler

def _fetch(base: str, path: str, timeout: float) -> tuple[int, str]:
    from urllib.error import HTTPError, URLError
    from urllib.request import urlopen

    try:
        with urlopen(base + path, timeout=timeout) as resp:  # noqa: S310 - host 127.0.0.1 buatan sendiri
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return exc.code, ""
    except URLError as exc:
        raise BenchError(f"server task tidak menjawab: {exc}") from exc

def _run_against_server(task: dict[str, Any], base: str, *, timeout: float) -> list[dict[str, Any]]:
    """Hypothesis yang rutenya hidup (<400) dicatat sebagai temuan.

    Itu bukti minimum yang dimiliki harness offline: rute ada dan merespons
    sukses. Tidak ada klaim dampak di sini — klasifikasi tetap urusan
    `veracity`/`verify_gate` di alur sungguhan.
    """
    found: list[dict[str, Any]] = []
    for item in task.get("findings") or []:
        path = str(item.get("path") or "/")
        status, body = _fetch(base, path, timeout)
        if status >= 400:
            continue
        found.append({
            "id": item.get("id", ""),
            "hypothesis": item.get("hypothesis", ""),
            "vuln_class": item.get("vuln_class", "unknown"),
            "path": path,
            "severity": item.get("severity", "medium"),
            "evidence": f"GET {path} -> {status} ({len(body)} byte)",
        })
    return found

def _hits(findings: list[dict[str, Any]], truth: list[dict[str, Any]]) -> int:
    """Satu truth dihitung sekali, walau harness melaporkan duplikat."""
    used: set[int] = set()
    hits = 0
    for want in truth:
        for index, got in enumerate(findings):
            if index in used:
                continue
            if str(got.get("vuln_class")) != str(want.get("vuln_class")):
                continue
            want_path = want.get("path")
            if want_path and str(got.get("path")) != str(want_path):
                continue
            used.add(index)
            hits += 1
            break
    return hits

def run_task(task_dir: Path, *, timeout: float = DEFAULT_TIMEOUT_S) -> BenchResult:
    task_dir = Path(task_dir)
    task = _load(task_dir / TASK_FILE, label=TASK_FILE)
    truth_doc = _load(task_dir / TRUTH_FILE, label=f"{TRUTH_FILE} (ground truth)")
    truth = truth_doc.get("findings")
    if not isinstance(truth, list):
        raise BenchError(f"{task_dir / TRUTH_FILE}: 'findings' harus list")

    name = str(task.get("name") or task_dir.name)
    responses = {str(k): v for k, v in (task.get("responses") or {}).items()}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(responses))
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name=f"bench-{name}")
    started = time.monotonic()
    target = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        thread.start()
        findings = _run_against_server(task, target, timeout=timeout)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
    duration = round(time.monotonic() - started, 3)

    hits = _hits(findings, truth)
    return BenchResult(
        task=name,
        passed=hits == len(truth) and len(findings) == len(truth),
        findings=findings,
        duration_s=duration,
        ground_truth_hits=hits,
        truth_total=len(truth),
        targets=[target],
    )

def score(results: list[BenchResult]) -> dict[str, float]:
    """Skor agregat: precision/recall dari jumlah, bukan rata-rata per task.

    Rata-rata per task membuat task kecil berbobot sama dengan task besar; untuk
    benchmark ini jumlah temuan yang lebih jujur.
    """
    findings = sum(len(r.findings) for r in results)
    hits = sum(r.ground_truth_hits for r in results)
    truth = sum(r.truth_total for r in results)
    return {
        "tasks": len(results),
        "passed": sum(1 for r in results if r.passed),
        "findings": findings,
        "hits": hits,
        "truth": truth,
        "precision": round(hits / findings, 4) if findings else (1.0 if not truth else 0.0),
        "recall": round(hits / truth, 4) if truth else 1.0,
    }

def run_all(tasks_root: Path, *, timeout: float = DEFAULT_TIMEOUT_S) -> tuple[list[BenchResult], dict[str, float]]:
    results = [run_task(d, timeout=timeout) for d in task_dirs(tasks_root)]
    return results, score(results)
