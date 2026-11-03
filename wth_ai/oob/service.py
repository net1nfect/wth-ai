"""OOBCallbackService — API tingkat operator/agent untuk Callback Center (F2).

Isi: satu `OOBStore` + satu listener opsional + format teks yang dipakai bersama
oleh slash command `/oob`, tool agent `oob_create`/`oob_check`, dan test.

Kenapa singleton (`get_oob_service`):
* listener harus tetap hidup lintas pemanggilan (start -> tool -> check);
* token bertahan di `$WTH_HOME/oob/` sehingga restart proses tidak menghapus
  riwayat, hanya melepas listener-nya (token lama tetap bisa dicek).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from wth_ai.oob.server import DEFAULT_HOST, OOBCallbackServer
from wth_ai.oob.store import OOBStore
from wth_ai.terminal.models import TerminalResult


def format_hits(entry: dict[str, Any], hits: list[dict[str, Any]], *, max_items: int = 20) -> str:
    """Format manusiawi untuk hit; dipakai slash command maupun tool agent."""
    label = entry.get("label") or "(tanpa label)"
    token = str(entry.get("token", ""))
    shown = token if len(token) <= 16 else f"{token[:16]}…"
    status = "EXPIRED" if entry.get("expired") else "aktif"
    header = f"OOB CALLBACK — {label}"
    lines = [
        header,
        f"token: {shown} · {status}"
        + (f" · expires {entry['expires_at']}" if entry.get("expires_at") else ""),
        f"hits: {len(hits)}",
    ]
    for rec in hits[-max_items:]:
        head = f"- {rec.get('ts', '?')} | {rec.get('method', '?')} {rec.get('path', '')}"
        if rec.get("query"):
            head += f"?{rec['query']}"
        if rec.get("remote"):
            head += f" | from {rec['remote']}"
        lines.append(head)
        headers = rec.get("headers") or {}
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            lines.append(f"  User-Agent: {str(ua)[:160]}")
        for key, value in [(k, v) for k, v in headers.items() if k.lower().startswith("x-")][:5]:
            lines.append(f"  {key}: {str(value)[:160]}")
        if rec.get("body"):
            lines.append(f"  body: {str(rec['body'])[:240]}")
    if len(hits) > max_items:
        lines.append(f"... +{len(hits) - max_items} hit lebih lama (lihat interactions.jsonl)")
    return "\n".join(lines)


class OOBCallbackService:
    def __init__(self, *, store: OOBStore | None = None, host: str | None = None, port: int | None = None) -> None:
        self.store = store or OOBStore()
        try:
            env_port = int(os.environ.get("WTH_OOB_PORT", "0") or 0)
        except ValueError:
            env_port = 0
        self.host = host or os.environ.get("WTH_OOB_HOST") or DEFAULT_HOST
        self._port_pref = env_port if port is None else int(port)
        self.server: OOBCallbackServer | None = None

    @property
    def running(self) -> bool:
        return self.server is not None and self.server.running

    @property
    def base_url(self) -> str:
        srv = self.server
        return srv.url_base if (srv is not None and srv.running) else ""

    def start(self, *, host: str | None = None, port: int | None = None) -> dict[str, Any]:
        if self.running:
            return self.status()
        self.server = OOBCallbackServer(
            self.store,
            host=host or self.host,
            port=self._port_pref if port is None else int(port),
        )
        self.server.start()
        self.host = self.server.host
        return self.status()

    def stop(self) -> bool:
        if self.server is None:
            return False
        self.server.stop()
        return True

    def create(
        self,
        *,
        label: str = "",
        ttl_minutes: int | None = None,
        session_id: str = "",
        target: str = "",
    ) -> dict[str, Any]:
        entry = self.store.create_token(label=label, ttl_minutes=ttl_minutes, session_id=session_id, target=target)
        srv = self.server
        entry["url"] = srv.url_for(entry["token"]) if (srv is not None and srv.running) else ""
        return entry

    def record_as_transcript(
        self,
        transcripts: Any,
        *,
        token: str,
        entry: dict[str, Any] | None = None,
        hits: list[dict[str, Any]] | None = None,
    ) -> str:
        """Tulis hit sebagai transcript `[TX:...]`.

        Ini satu-satunya cara hit OOB menjadi bukti yang boleh dikutip agent
        (TTST): tanpa transcript, klaim callback hanya narasi.
        """
        entry = entry or self.store.get_token(token) or {"token": token, "label": ""}
        hits = hits if hits is not None else self.store.hits(token)
        tx_id = f"TX-{uuid4().hex[:10]}"
        transcripts.store(
            TerminalResult(
                transcript_id=tx_id,
                actor="oob",
                purpose=f"OOB callback check — {entry.get('label') or 'callback'}",
                command=f"oob-check {token}",
                stdout=format_hits(entry, hits),
                stderr="",
                exit_code=0,
                url=self.base_url,
                method="GET",
                status_code=200,
            )
        )
        return tx_id

    def status(self) -> dict[str, Any]:
        tokens = self.store.list_tokens(limit=500)
        open_tokens = [t for t in tokens if not t.get("expired")]
        return {
            "running": self.running,
            "host": self.server.host if self.server else self.host,
            "port": self.server.port if self.server else 0,
            "base_url": self.base_url,
            "tokens_total": len(tokens),
            "tokens_open": len(open_tokens),
            "hits_total": self.store.total_hits(),
        }


_SERVICE: OOBCallbackService | None = None
_SERVICE_HOME: str | None = None


def _resolved_home() -> str:
    return os.environ.get("WTH_HOME") or str(Path.home() / ".wth-ai")


def get_oob_service() -> OOBCallbackService:
    """Singleton per-`WTH_HOME`.

    Kalau `WTH_HOME` berubah (antar test / antar workspace), singleton lama
    dilepas dan listener-nya dimatikan — supaya token tidak pernah tercampur
    antar home.
    """
    global _SERVICE, _SERVICE_HOME
    home = _resolved_home()
    if _SERVICE is None or _SERVICE_HOME != home:
        if _SERVICE is not None:
            try:
                _SERVICE.stop()
            except Exception:  # noqa: BLE001
                pass
        _SERVICE = OOBCallbackService()
        _SERVICE_HOME = home
    return _SERVICE


def reset_oob_service() -> None:
    """Helper test/lifecycle: lepas singleton (dan matikan listener-nya)."""
    global _SERVICE, _SERVICE_HOME
    if _SERVICE is not None:
        try:
            _SERVICE.stop()
        except Exception:  # noqa: BLE001
            pass
    _SERVICE = None
    _SERVICE_HOME = None
