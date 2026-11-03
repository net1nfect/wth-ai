"""Gateway daemon — worker, integrations, RPC, session cache."""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

from wth_ai.config import WTHConfig
from wth_ai.events import get_event_bus
from wth_ai.gateway.rpc import GatewayRpcServer, gateway_pid_path, gateway_socket_path
from wth_ai.gateway.background import (
    assert_not_running,
    cleanup_stale_runtime,
    write_gateway_status,
)
from wth_ai.gateway.router import GatewayRouter
from wth_ai.gateway.session_cache import SessionCache
from wth_ai.integrations.manager import IntegrationManager
from wth_ai.jobs.manager import JobManager
from wth_ai.jobs.worker import WorkerEngine
from wth_ai.workspace import init_workspace, wth_home


class GatewayDaemon:
    """Long-lived Hermes-style gateway process."""

    def __init__(self, config: WTHConfig | None = None) -> None:
        init_workspace()
        self.config = config or WTHConfig.load()
        self.bus = get_event_bus()
        self.job_manager = JobManager(self.config, bus=self.bus)
        self.worker = WorkerEngine(self.job_manager)
        self.integrations = IntegrationManager(self.config, bus=self.bus)
        self.cache = SessionCache(self.config)
        self.router = GatewayRouter(self.cache)
        self._rpc: GatewayRpcServer | None = None
        self._worker_thread: threading.Thread | None = None
        self._cron_thread: threading.Thread | None = None
        self._running = False
        self._plugins_loaded = False
        self._audit_sink: Any = None  # WU-104: set in start() (guarded); pre-init so it's always a valid attr

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "ping":
            return True
        session_key = str(params.get("session_key", "cli:default"))
        if method == "handle_input":
            text = str(params.get("text", ""))
            result = self.router.handle(session_key, text, fresh=bool(params.get("fresh")))
            self._index_fts(session_key, text, result)
            return result
        if method == "get_status":
            return self.router.status(session_key)
        if method == "gateway_status":
            return self.router.gateway_status(self.worker.status())
        if method == "new_session":
            self.cache.remove(session_key)
            orch = self.cache.get(session_key, fresh=True)
            return orch.state.session_id
        if method == "list_sessions":
            return self.cache.list_keys()
        raise ValueError(f"unknown method: {method}")

    def _index_fts(self, session_key: str, user_text: str, response: str) -> None:
        try:
            from wth_ai.memory.fts import SessionFtsIndex
            idx = SessionFtsIndex()
            idx.index_turn(session_key, user_text, response)
        except Exception:
            pass

    def _load_plugins(self) -> None:
        if self._plugins_loaded:
            return
        try:
            from wth_ai.plugins.manager import PluginManager
            PluginManager(self.config, bus=self.bus).load_all()
        except Exception:
            pass
        self._plugins_loaded = True

    def _worker_loop(self) -> None:
        while self._running:
            try:
                self.worker.run_once()
            except Exception:
                pass
            time.sleep(2)

    def _cron_loop(self) -> None:
        while self._running:
            try:
                from wth_ai.cron.scheduler import CronScheduler
                CronScheduler(self.job_manager).tick()
            except Exception:
                pass
            time.sleep(30)

    def start(self, *, foreground: bool = True) -> None:
        cleanup_stale_runtime()
        assert_not_running()
        self._running = True
        self._load_plugins()
        try:
            from wth_ai.cron.store import CronStore
            CronStore().migrate_from_watch()
        except Exception:
            pass
        try:
            from wth_ai.rpc.execute_server import ExecuteRpcServer
            ExecuteRpcServer().start_background()
        except Exception:
            pass
        # WU-104: subscribe a durable AuditLogSink to the process bus for the
        # daemon's lifetime. This advances WU-105's deliberately-deferred
        # auto-wiring, scoped to the SERVER context specifically (a long-lived
        # daemon that starts ExecuteRpcServer, jobs, recon -- exactly where a
        # durable audit trail belongs), NOT a blanket wire into every one-shot
        # CLI invocation. Guarded like the ExecuteRpcServer start above: a
        # sink init failure (e.g. read-only disk) must never abort the daemon.
        try:
            from wth_ai.audit_log import AuditLogSink
            self._audit_sink = AuditLogSink()
            self.bus.subscribe(self._audit_sink)
        except Exception:
            self._audit_sink = None
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=False, name="gateway-worker")
        self._worker_thread.start()
        self._cron_thread = threading.Thread(target=self._cron_loop, daemon=False, name="gateway-cron")
        self._cron_thread.start()
        self._rpc = GatewayRpcServer(gateway_socket_path(), self._dispatch)
        self._rpc.start()
        self._write_pid(os.getpid())
        write_gateway_status("running", foreground=foreground)
        if foreground:
            signal.signal(signal.SIGINT, lambda *_: self.stop())
            signal.signal(signal.SIGTERM, lambda *_: self.stop())
            while self._running:
                time.sleep(1)
        else:
            # Caller must keep process alive (use spawn_background for true daemonize).
            while self._running:
                time.sleep(1)

    def stop(self) -> None:
        self._running = False
        if self._rpc:
            self._rpc.stop()
        self._clear_pid()
        write_gateway_status("stopped")

    def _write_pid(self, pid: int) -> None:
        path = gateway_pid_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(pid), encoding="utf-8")

    def _clear_pid(self) -> None:
        gateway_pid_path().unlink(missing_ok=True)
