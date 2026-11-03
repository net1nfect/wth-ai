"""Regression test — WU-210 (DATA-04): `WTHConfig.write_env_file()` must
chmod 600 the plaintext-secret `.env` it writes.

Bug asli: `write_env_file()` menulis OPENROUTER_API_KEY/DISCORD_BOT_TOKEN/dll
sebagai plaintext ke `~/.wth-ai/.env` tapi TIDAK PERNAH memanggil
`secure_file()` — file dibuat dengan permission default umask
(sering world/group-readable), padahal `USER_CONFIG_PATH` dan `vault.key`
sudah di-chmod 600. Fix satu-baris: `secure_file(USER_ENV_PATH)` setelah
write.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")


def test_write_env_file_is_chmod_600(monkeypatch):
    import wth_ai.config as cfg

    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / ".env"
        monkeypatch.setattr(cfg, "USER_ENV_PATH", env_path)
        c = cfg.WTHConfig()
        c.secrets.openrouter_api_key = "sk-secret-should-be-protected"
        written = c.write_env_file()

        assert written == env_path
        assert env_path.exists()
        mode = stat.S_IMODE(env_path.stat().st_mode)
        # tidak boleh readable/writable oleh group atau other
        assert mode & 0o077 == 0, f"perms {oct(mode)} membocorkan .env ke group/other"
        assert mode & 0o600 == 0o600, f"owner harus rw, dapat {oct(mode)}"


def test_write_env_file_still_writes_content(monkeypatch):
    """Compat: fix permission tidak mengubah isi/among yang ditulis."""
    import wth_ai.config as cfg

    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / ".env"
        monkeypatch.setattr(cfg, "USER_ENV_PATH", env_path)
        c = cfg.WTHConfig()
        c.secrets.openrouter_api_key = "sk-xyz"
        c.write_env_file()
        content = env_path.read_text()
        assert "OPENROUTER_API_KEY=sk-xyz" in content
