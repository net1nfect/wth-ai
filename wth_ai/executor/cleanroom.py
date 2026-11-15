"""Clean-room runner — isolasi eksperimen TANPA Docker (keputusan operator).

Bukan isolasi kernel (tidak ada container/ns/jail) — dan itu di-deklarasi
JUJUR. Yang dijanjikan:
  * cwd throwaway (room/work)                 → artefak tidak mengotori repo
  * HOME throwaway (room/home)                → script tidak menyentuh config asli
  * TMPDIR throwaway                          → file temp tidak bocor keluar
  * env dipangkas: semua `WTH_*` dibuang      → mode/tenant tidak bocor ke child
  * (opsional) venv fresh di dalam room       → dependensi npm/pip bersih per-experimen
Pakai pas-pasan untuk: uji payload generator, parser hasil scanner, self_improve
deadline script — bukan untuk threat nyata dari binary yang tidak dipercaya.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: env yang DIBOLEHKAN masuk ke child. Segala hal lain dibuang (termasuk WTH_*).
_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TERM", "SHELL", "XDG_RUNTIME_DIR")


@dataclass
class CleanRoom:
    root: Path
    home: Path
    work: Path
    tmp: Path
    _env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls, *, label: str = "run", venv: bool = False) -> "CleanRoom":
        from wth_ai.workspace import wth_home

        room_id = f"{label}-{secrets.token_hex(4)}"
        root = wth_home() / "cleanroom" / room_id
        home = root / "home"
        work = root / "work"
        tmp = root / "tmp"
        for d in (home, work, tmp):
            d.mkdir(parents=True, exist_ok=True)

        env: dict[str, str] = {}
        for key in _ENV_KEEP:
            value = os.environ.get(key)
            if value:
                env[key] = value
        env["HOME"] = str(home)
        env["TMPDIR"] = str(tmp)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        if venv:
            import venv as _venv

            venv_dir = root / "venv"
            builder = _venv.EnvBuilder(with_pip=True)
            builder.create(str(venv_dir))
            venv_bin = venv_dir / "bin"
            if venv_bin.exists():
                env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
                env["VIRTUAL_ENV"] = str(venv_dir)
        return cls(root=root, home=home, work=work, tmp=tmp, _env=env)

    def run(self, cmd: str, *, timeout: int = 60) -> tuple[str, str, int]:
        """Eksekusi di dalam room. Return (stdout, stderr, exit_code) — hasil
        dipikul caller untuk disimpan ke transcript."""
        if not self._env.get("PATH"):
            raise RuntimeError("CleanRoom._env.PATH kosong — env dasar host hilang?")
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(self.work), env=self._env,
                capture_output=True, text=True, timeout=timeout,
            )
            return proc.stdout or "", proc.stderr or "", proc.returncode
        except subprocess.TimeoutExpired as exc:
            return str(exc.stdout or ""), str(exc.stderr or "timeout"), 124
        except OSError as exc:
            return "", str(exc), 127

    def python_mutated_env_example(self) -> str:  # pragma: no cover - dokumentasi
        return "python3 - <<'EOF'\nprint('hi')\nEOF"

    def cleanup(self) -> int:
        """Hapus room; return jumlah file yang dihapus (±, best effort)."""
        count = 0
        for _root, _dirs, files in os.walk(self.root):
            count += len(files)
        shutil.rmtree(self.root, ignore_errors=True)
        return count


def has_cleanroom_support() -> bool:
    return sys.version_info >= (3, 11) and (os.name == "posix")
