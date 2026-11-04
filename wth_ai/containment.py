"""Sesi red team di dalam container terkurung.

Modul ini hanya MEMBANGUN argv; menjalankannya adalah tugas pemanggil (CLI /
TUI). Dipisah supaya flag keamanan bisa diuji tanpa docker di CI — test lama
tidak boleh bergantung pada daemon docker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field

LAB_NETWORK = "wth-lab-internal"
#: Tag biasa sebagai default supaya `docker run` jalan tanpa registry internal.
#: Untuk engagement nyata, pin digest lewat WTH_LAB_IMAGE (mis.
#: "python@sha256:<64 hex>") — digest tidak di-hardcode di sini karena nilainya
#: harus diverifikasi terhadap registry, bukan dikarang.
DEFAULT_IMAGE = os.environ.get("WTH_LAB_IMAGE", "python:3.12-slim")


@dataclass(frozen=True)
class SandboxSpec:
    image: str = DEFAULT_IMAGE
    network: str = LAB_NETWORK
    memory: str = "512m"
    cpus: str = "1.0"
    pids_limit: int = 128
    tmpfs_size: str = "64m"
    extra_mounts: tuple[str, ...] = field(default_factory=tuple)

    def docker_args(self, command: list[str]) -> list[str]:
        mounts: list[str] = []
        for mount in self.extra_mounts:
            mounts += ["-v", mount]
        return [
            "docker", "run", "--rm", "-i",
            "--network", self.network,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.pids_limit),
            "--memory", self.memory,
            "--cpus", self.cpus,
            "--read-only",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,size={self.tmpfs_size}",
            *mounts,
            self.image,
            *command,
        ]


def network_create_args(name: str = LAB_NETWORK) -> list[str]:
    """Network internal: tanpa gateway keluar, tanpa published port."""
    return ["docker", "network", "create", "--internal", name]


def docker_available() -> bool:
    return shutil.which("docker") is not None


def ensure_network(name: str = LAB_NETWORK, *, run=subprocess.run) -> bool:
    """True bila network ada (dibuat bila perlu). Tidak melempar."""
    if not docker_available():
        return False
    probe = run(
        ["docker", "network", "inspect", name],
        capture_output=True, text=True, timeout=20,
    )
    if probe.returncode == 0:
        return True
    made = run(network_create_args(name), capture_output=True, text=True, timeout=30)
    return made.returncode == 0