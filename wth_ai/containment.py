"""Sesi red team di dalam container terkurung.

Modul ini hanya MEMBANGUN argv; menjalankannya adalah tugas pemanggil (CLI /
TUI). Dipisah supaya flag keamanan bisa diuji tanpa docker di CI — test lama
tidak boleh bergantung pada daemon docker.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

#: Digest Docker = `sha256:<64 hex>`. Sintaks ini diverifikasi untuk menolak
#: typo sejak awal (sebelum `docker run` gagal dengan pesan yang samar).
_DIGEST_RE = re.compile(r"^[0-9a-fA-F]{64}$")

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
        if "@sha256:" in self.image:
            algo, _, digest = self.image.partition("@sha256:")
            if not algo or not _DIGEST_RE.fullmatch(digest):
                raise ValueError(
                    f"image digest tidak valid: {self.image!r} "
                    "(harus <image>@sha256:<64 hex>)"
                )
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


def resolve_image_digest(
    image: str, *, run=subprocess.run,
) -> tuple[bool, str]:
    """Resolusi tag -> digest untuk `sandbox run --image`.

    B5: pin digest saat `--image` dipakai — konversi tag → digest SEKALI,
    tolak bila tidak bisa diverifikasi (fail-closed). Kalau image sudah berupa
    `name@sha256:<hex>`, sintaksnya diverifikasi dan tidak perlu menanyai
    daemon. Tidak pernah meng-hardcode digest: nilainya datang dari daemon
    Docker (RepoDigests) atau dari input pengguna.

    Return `(ok, alasan|image_pinned)`.
    """
    if "@sha256:" in image:
        algo, _, digest = image.partition("@sha256:")
        if not algo or not _DIGEST_RE.fullmatch(digest):
            return False, f"image digest tidak valid: {image!r} (harus <image>@sha256:<64 hex>)"
        return True, image
    if not docker_available():
        return False, "docker tidak ditemukan di PATH (tidak bisa verifikasi digest)"
    probe = run(
        ["docker", "image", "inspect", "--format",
         "{{index .RepoDigests 0}}", image],
        capture_output=True, text=True, timeout=30,
    )
    if probe.returncode != 0:
        return False, f"gagal resolve digest untuk {image!r}: {probe.stderr.strip()}"
    resolved = probe.stdout.strip()
    if "@sha256:" not in resolved:
        return False, f"image {image!r} tidak punya digest (Repodigests kosong)"
    algo, _, digest = resolved.partition("@sha256:")
    if not algo or not _DIGEST_RE.fullmatch(digest):
        return False, f"digest terresolve tidak valid: {resolved!r}"
    return True, resolved


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