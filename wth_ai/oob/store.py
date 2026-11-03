"""OOB interaction store — token registry + hit log, untuk Callback Center (F2).

Kenapa terpisah dari listener-nya:
* Listener hanya boleh menulis lewat satu jalur (`record_interaction`) supaya
  redaksi, cap ukuran, dan format penyimpanan konsisten di semua metode HTTP.
* Token dan hit disimpan di bawah `$WTH_HOME/oob/` (bukan di repo, bukan
  global): operator bisa membaca `tokens.json` + `interactions.jsonl` kapan
  pun, tanpa database dan tanpa server yang harus hidup.

Prinsip yang dijaga (lihat `docs/planning/04-f2-callback-center.md`):
* TTL dihitung LIVE dari `expires_at` saat dibaca — tidak ada proses yang
  menghapus diam-diam; token kedaluwarsa tetap terlihat berlabel `expired`.
* Hit SETELAH kedaluwarsa tetap TERCATAT dan tetap tersedia sebagai bukti:
  callback yang telat tetap bukti bahwa server target menghubungi kita.
* Semua isi yang masuk di-redaksi dengan `redact_secrets` kanonik — body dan
  nilai header — karena apa yang dibawa hit callback tidak bisa kita tebak.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from wth_ai.models import utc_now
from wth_ai.redaction import redact_secrets

DEFAULT_TTL_MINUTES = 60
MAX_INTERACTIONS_RETURNED = 200
MAX_RECENT_HITS = 50
MAX_BODY_STORED = 4096
MAX_HEADERS = 25
MAX_HEADER_VALUE = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class OOBStore:
    """Token registry + interaction log di `$WTH_HOME/oob/`."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.dir = base_dir or Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai")) / "oob"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tokens_path = self.dir / "tokens.json"
        self.interactions_path = self.dir / "interactions.jsonl"
        self._lock = threading.Lock()

    # -- tokens ----------------------------------------------------------- #

    def _load_tokens(self) -> dict[str, dict[str, Any]]:
        if not self.tokens_path.exists():
            return {}
        try:
            return json.loads(self.tokens_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_tokens(self, data: dict[str, dict[str, Any]]) -> None:
        self.tokens_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def create_token(
        self,
        *,
        label: str = "",
        ttl_minutes: int | None = None,
        session_id: str = "",
        target: str = "",
    ) -> dict[str, Any]:
        ttl = DEFAULT_TTL_MINUTES if ttl_minutes is None else int(ttl_minutes)
        now = _now()
        entry = {
            "token": secrets.token_urlsafe(16),
            "label": (label or "").strip()[:80],
            "created_at": _iso(now),
            "expires_at": _iso(now + timedelta(minutes=ttl)),
            "ttl_minutes": ttl,
            "session_id": session_id,
            "target": target,
        }
        with self._lock:
            data = self._load_tokens()
            data[entry["token"]] = entry
            self._save_tokens(data)
        return dict(entry)

    def get_token(self, token: str) -> dict[str, Any] | None:
        entry = self._load_tokens().get(token or "")
        if not entry:
            return None
        out = dict(entry)
        out["expired"] = self._is_expired(out)
        return out

    @staticmethod
    def _is_expired(entry: dict[str, Any]) -> bool:
        try:
            return _now() >= datetime.fromisoformat(str(entry.get("expires_at", "")))
        except ValueError:
            return False

    def list_tokens(self, *, limit: int = 50) -> list[dict[str, Any]]:
        counts = self._hit_counts()
        out: list[dict[str, Any]] = []
        for entry in self._load_tokens().values():
            item = dict(entry)
            item["expired"] = self._is_expired(item)
            item["hits"] = counts.get(item["token"], 0)
            out.append(item)
        out.sort(key=lambda e: str(e.get("created_at", "")), reverse=True)
        return out[:limit]

    def clear_expired(self) -> int:
        """Hapus token kedaluwarsa dari registry; interaksi TIDAK disentuh."""
        with self._lock:
            data = self._load_tokens()
            keep = {t: e for t, e in data.items() if not self._is_expired(e)}
            removed = len(data) - len(keep)
            if removed:
                self._save_tokens(keep)
        return removed

    # -- interactions ------------------------------------------------------ #

    def record_interaction(
        self,
        *,
        token: str,
        method: str,
        path: str,
        query: str = "",
        remote: str = "",
        headers: dict[str, Any] | None = None,
        body: str = "",
    ) -> dict[str, Any]:
        """Satu-satunya jalur tulis hit; redaksi + cap diterapkan di sini."""
        with self._lock:
            matched = token in self._load_tokens()
        record = {
            "ts": utc_now(),
            "token": (token or "")[:120],
            "matched": matched,
            "method": (method or "").upper()[:12],
            "path": (path or "")[:500],
            "query": (query or "")[:500],
            "remote": (remote or "")[:120],
            "headers": self._clean_headers(headers or {}),
            "body": redact_secrets((body or "")[:MAX_BODY_STORED]),
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.interactions_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return record

    @staticmethod
    def _clean_headers(headers: dict[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for i, (key, value) in enumerate(headers.items()):
            if i >= MAX_HEADERS:
                break
            out[str(key)[:80]] = redact_secrets(str(value)[:MAX_HEADER_VALUE])
        return out

    def _iter_records(self) -> Iterator[dict[str, Any]]:
        if not self.interactions_path.exists():
            return
        for line in self.interactions_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

    def _hit_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rec in self._iter_records():
            t = str(rec.get("token", ""))
            counts[t] = counts.get(t, 0) + 1
        return counts

    def hits(self, token: str, *, limit: int = MAX_INTERACTIONS_RETURNED) -> list[dict[str, Any]]:
        out = [r for r in self._iter_records() if r.get("token") == token]
        return out[-limit:]

    def recent_hits(self, *, limit: int = MAX_RECENT_HITS) -> list[dict[str, Any]]:
        out = list(self._iter_records())
        return out[-limit:]

    def total_hits(self) -> int:
        return sum(1 for _ in self._iter_records())
