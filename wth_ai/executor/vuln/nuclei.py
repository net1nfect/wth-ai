"""Nuclei scanner executor — marshal i各组-regex output JSON → evidence.

GAP-001: `nuclei` binary tersedia (`which nuclei` OK), tapi output scanner
tidak pernah diterikan ke findings graph — agent hanya bisa `run_shell` manual
dan hasil parser takli yang locald. Executor ini menyambung: jalankan
`nuclei -u <target> -json -silent -nc`, parse setiap line JSON, mapping severity
→ risk, tiap temuan jadi entry evidence, hasil jumlah tersimpan di `observations`.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask

#: severity nuclei → risk internal
SEVERITY_MAP = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "low",
    "unknown": "low",
}

MAX_FINDINGS = 200

# Flag JSON berbeda antar generasi: v2 = "-json", v3 = "-j/-jsonl". Di-cache
# per proses setelah autodetect pertama (lihat `nuclei_run`).
_NUCLEI_JSON_FLAG: str | None = None


def nuclei_run(target: str, extra: list[str] | None = None) -> tuple[int, str, str]:
    """Jalankan nuclei JSON mode dengan autodetect flag. Shared executor+arena."""
    global _NUCLEI_JSON_FLAG
    exe = shutil.which("nuclei") or "/usr/local/bin/nuclei"
    if _NUCLEI_JSON_FLAG is None:
        for flag in ("-json", "-jsonl"):
            try:
                probe = subprocess.run(
                    [exe, "-u", target, flag, "-silent", "-nc", *(extra or [])],
                    capture_output=True, text=True, timeout=600,
                )
            except subprocess.TimeoutExpired as exc:
                return 1, str(exc.stdout or ""), str(exc.stderr or "")
            joined = (probe.stdout or "") + (probe.stderr or "")
            if "flag provided but not defined" in joined:
                continue
            _NUCLEI_JSON_FLAG = flag
            return probe.returncode, probe.stdout, probe.stderr
        _NUCLEI_JSON_FLAG = "-json"  # semua fail: biarkan error asli muncul di caller
    proc = subprocess.run(
        [exe, "-u", target, _NUCLEI_JSON_FLAG, "-silent", "-nc", *(extra or [])],
        capture_output=True, text=True, timeout=600,
    )
    return proc.returncode, proc.stdout, proc.stderr


class NucleiExecutor(BaseVulnExecutor):
    vuln_type = "nuclei"

    def _run_nuclei(self, target: str) -> tuple[int, str, str]:
        return nuclei_run(target)

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        target = task.target
        if not target.startswith("http"):
            target = f"https://{target}"
        try:
            code, out, err = self._run_nuclei(target)
        except FileNotFoundError:
            return self._result(
                task, status="failed", summary="nuclei binary tidak ditemukan",
                evidence=[], observations=["install: go install -v github.com/projectdiscovery/nuclei/v2/cmd/nuclei@latest"],
                risk="low",
            )
        except subprocess.TimeoutExpired:
            return self._result(
                task, status="failed", summary="nuclei timeout (600s)",
                evidence=[], observations=["Template scan terlalu lambat — pecah target atau batasi tags"],
                risk="low",
            )

        findings: list[dict[str, object]] = []
        bad_lines = 0
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            finding = {
                "template": data.get("template-id") or data.get("templateID") or "",
                "name": (data.get("info", {}) or {}).get("name", ""),
                "severity": (data.get("info", {}) or {}).get("severity", "unknown"),
                "matched_at": data.get("matched-at") or data.get("host") or target,
                "type": data.get("type", ""),
            }
            finding["url"] = finding["matched_at"]
            findings.append(finding)
            if len(findings) >= MAX_FINDINGS:
                break

        counts: dict[str, int] = {}
        for f in findings:
            sev = str(f.get("severity", "unknown")).lower()
            counts[sev] = counts.get(sev, 0) + 1

        evidence: list[str] = []
        for f in findings[:MAX_FINDINGS]:
            sev = str(f.get("severity", "unknown")).upper()
            evidence.append(
                f"nuclei[{sev}] {f.get('name', '')} (template={f.get('template')}) "
                f"@ {str(f.get('url'))[:120]}"
            )

        observations = [f"total={len(findings)}", 
                        f"counts=" + ",".join(f"{k}:{v}" for k, v in counts.items()) or "-"]
        if bad_lines:
            observations.append(f"unparsed_lines={bad_lines}")
        if code != 0:
            observations.append(f"exit={code} stderr={err[:200]}")

        if counts.get("critical"):
            risk = "critical"
        elif counts.get("high"):
            risk = "high"
        elif counts.get("medium"):
            risk = "medium"
        else:
            risk = "low"
        summary = (
            f"nuclei scan {target} — {len(findings)} temuan "
            f"({', '.join(f'{k}={v}' for k, v in counts.items()) or 'tidak ada'})"
        )
        return self._result(task, status="success", summary=summary,
                            evidence=evidence, observations=observations, risk=risk)
