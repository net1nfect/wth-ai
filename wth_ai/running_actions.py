"""`RunningActionRegistry` — track proses latar agar kill-switch benar-benar bisa membunuhnya.

Konteks (F1 / sisa WU-106): interrupt-check in-process (`ctx.stopped`) hanya
menghentikan ITERASI loop berikutnya; proses subprocess yang sudah telanjur
berjalan (shell, recon tool, eksploit) tidak tersentuh — WU-108 mencatatnya
eksplisit: "membunuh PROSES nyata butuh RunningActionRegistry (WU-106)".

Desain:
* Yang disimpan adalah **process group** (`pgid`), bukan cuma PID: dengan
  `start_new_session=True` di titik spawn, anak jadi leader group-nya sendiri
  sehingga SATU `os.killpg` menjangkau seluruh tree (anak-cucu) — membunuh PID
  langsung saja meninggalkan cucu yang orphan (kelas bug sama dengan pipe
  timeout WU-108).
* `kill_all()` bertingkat: SIGTERM dulu (beri kesempatan cleanup), tunggu
  `grace_seconds`, lalu SIGKILL untuk yang masih hidup. Hasil per-aksi
  dilaporkan (`terminated`/`skipped`/`errors`) — bukan fire-and-forget.
* **Guard wajib**: kill_all TIDAK PERNAH menyentuh process-group milik proses
  ini sendiri (`os.getpgid(0)`) — salah-registrasi tidak boleh berubah jadi
  bunuh-diri satu agen.
* Tidak pernah raise: dipanggil dari jalur shutdown/interrupt, di mana crash
  justru memperburuk keadaan.

Entry yang mati di-unregister; entry yang gagal dibunuh DIPERTAHANKAN +
dilaporkan di `errors` supaya operator bisa inspeksi — tidak dihapus diam-diam.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

_KILL_POLL_SECONDS = 0.05
_SIGKILL_GRACE_SECONDS = 0.5


def kill_process_tree(proc: Any) -> None:
    """SIGKILL seluruh process group `proc` (anak-cucu ikut), fallback `proc.kill()`.

    Valid bila proses di-spawn dengan `start_new_session=True` (anak jadi leader
    group-nya sendiri, `pgid == pid`). Membunuh satu PID saja meninggalkan
    anak-cucunya hidup (kelas bug yang sama dengan pipe-timeout WU-108). Aman
    terhadap objek proses tiruan (test-seam) tanpa atribut `pid`.
    """
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int) and pid > 0 and hasattr(os, "killpg"):
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except OSError:
            pass
    try:
        proc.kill()
    except (OSError, AttributeError):
        pass


@dataclass(frozen=True)
class RunningAction:
    action_id: str
    pid: int
    pgid: int
    label: str
    actor: str
    started_at: str


class RunningActionRegistry:
    """In-process registry proses latar; satu instance global (`get_running_actions`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._actions: dict[str, RunningAction] = {}

    # -- lifecycle --------------------------------------------------------- #

    def register(self, pid: int, *, pgid: int | None = None, label: str = "", actor: str = "") -> str | None:
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            return None
        if pid_int <= 0:
            return None
        try:
            pgid_int = int(pgid) if pgid is not None else pid_int
        except (TypeError, ValueError):
            pgid_int = pid_int
        action = RunningAction(
            action_id=f"ACT-{uuid4().hex[:10]}",
            pid=pid_int,
            pgid=pgid_int,
            label=label[:120],
            actor=actor[:60],
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._actions[action.action_id] = action
        return action.action_id

    def unregister(self, action_id: str | None) -> bool:
        if not action_id:
            return False
        with self._lock:
            return self._actions.pop(action_id, None) is not None

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            actions = list(self._actions.values())
        actions.sort(key=lambda a: a.started_at)
        return [
            {
                "action_id": a.action_id,
                "pid": a.pid,
                "pgid": a.pgid,
                "label": a.label,
                "actor": a.actor,
                "started_at": a.started_at,
            }
            for a in actions
        ]

    def active_count(self) -> int:
        with self._lock:
            return len(self._actions)

    # -- kill switch ------------------------------------------------------- #

    @staticmethod
    def _group_alive(pgid: int) -> bool:
        """True bila masih ada proses HIDUP (bukan zombie) di group tsb.

        Pelajaran dari test pertama (gagal palsu): `killpg(pgid, 0)` tetap
        SUKSES untuk proses zombie yang belum di-reap -- padahal prosesnya
        sudah mati. Karena itu di Linux kita baca kolom state dari
        `/proc/<pid>/stat` dan mengabaikan `Z`; fallback ke probe killpg bila
        /proc tidak tersedia (non-Linux).
        """
        if os.path.isdir("/proc"):
            try:
                return RunningActionRegistry._group_alive_proc(pgid)
            except OSError:
                pass
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True

    @staticmethod
    def _group_alive_proc(pgid: int) -> bool:
        needle = str(pgid).encode()
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/stat", "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            # Format: "pid (comm) state ppid pgrp ..." -- comm bisa berisi
            # spasi/tanda kurung, jadi potong setelah ')' TERAKHIR.
            rparen = data.rfind(b")")
            if rparen < 0:
                continue
            fields = data[rparen + 2 :].split()
            if len(fields) < 3:
                continue
            state, pgrp = fields[0], fields[2]
            if pgrp == needle and state != b"Z":
                return True
        return False

    def _wait_group_dead(self, pgid: int, seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if not self._group_alive(pgid):
                return True
            time.sleep(_KILL_POLL_SECONDS)
        return not self._group_alive(pgid)

    def kill_all(self, *, grace_seconds: float = 1.0) -> dict[str, list[dict[str, Any]]]:
        """SIGTERM -> tunggu -> SIGKILL semua proses terdaftar; tidak pernah raise."""
        terminated: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        own_pgid = os.getpgid(0) if hasattr(os, "getpgid") else None

        with self._lock:
            actions = list(self._actions.values())

        for action in actions:
            base = {"action_id": action.action_id, "pid": action.pid, "pgid": action.pgid, "label": action.label}
            try:
                if action.pgid <= 0:
                    skipped.append({**base, "reason": "pgid tidak valid"})
                    continue
                if own_pgid is not None and action.pgid == own_pgid:
                    # Guard keselamatan: jangan pernah membunuh process-group kita sendiri.
                    skipped.append({**base, "reason": "process-group milik proses ini"})
                    continue
                if not hasattr(os, "killpg"):
                    try:
                        os.kill(action.pid, signal.SIGKILL)
                        self.unregister(action.action_id)
                        terminated.append({**base, "note": "kill per-PID (tanpa killpg)"})
                    except ProcessLookupError:
                        self.unregister(action.action_id)
                        terminated.append({**base, "note": "sudah mati"})
                    continue
                try:
                    os.killpg(action.pgid, signal.SIGTERM)
                except ProcessLookupError:
                    self.unregister(action.action_id)
                    terminated.append({**base, "note": "sudah mati"})
                    continue
                if not self._wait_group_dead(action.pgid, grace_seconds):
                    os.killpg(action.pgid, signal.SIGKILL)
                    if not self._wait_group_dead(action.pgid, _SIGKILL_GRACE_SECONDS):
                        errors.append({**base, "error": "masih hidup setelah SIGKILL"})
                        continue
                self.unregister(action.action_id)
                terminated.append({**base, "note": "dihentikan"})
            except OSError as exc:
                errors.append({**base, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - kill_all tidak pernah raise
                errors.append({**base, "error": f"{type(exc).__name__}: {exc}"})

        return {"terminated": terminated, "skipped": skipped, "errors": errors}


_REGISTRY: RunningActionRegistry | None = None


def get_running_actions() -> RunningActionRegistry:
    """Singleton in-process (proses hidup bersifat global, bukan per-`WTH_HOME`)."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = RunningActionRegistry()
    return _REGISTRY


def reset_running_actions() -> None:
    """Test helper: ganti registry baru. Proses yang masih terdaftar TIDAK dibunuh."""
    global _REGISTRY
    _REGISTRY = None
