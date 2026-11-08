"""Graf agen in-process dengan fan-out terbatas.

Kenapa thread, bukan proses: satu CLI/TUI sudah memegang satu orchestrator, dan
setiap node butuh objek yang sama (scope, permission mode, session state). Thread
cukup untuk fan-out <= MAX_NODES, dan node yang gagal tidak menjatuhkan induk.

Kenapa kill switch dipoll: flag `$WTH_HOME/runtime/KILL` adalah satu-satunya
sinyal lintas-proses yang sudah ada; thread in-process tidak punya pid sendiri
sehingga `KillSwitch.activate` (berbasis pid/process-group) tidak bisa
menjangkau mereka. Kita mempoll flag yang sama dan menandai node `killed`.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from wth_ai.agents.subagent.registry import MAX_DEPTH, get_spec
from wth_ai.killswitch import requested

#: Batas node per graph. Angka kecil disengaja: fan-out agen adalah biaya LLM
#: dan permukaan serangan; 8 adalah plafon yang masih bisa dibaca manusia di TUI.
MAX_NODES = 8

PENDING = "pending"
RUNNING = "running"
DONE = "done"
TIMEOUT = "timeout"
KILLED = "killed"
ERROR = "error"

STATUSES: tuple[str, ...] = (PENDING, RUNNING, DONE, TIMEOUT, KILLED, ERROR)
TERMINAL: frozenset[str] = frozenset({DONE, TIMEOUT, KILLED, ERROR})

#: Interval poll untuk `wait`. 10 ms cukup responsif untuk kill switch tanpa
#: memutar CPU saat runner yang dipakai adalah panggilan LLM berdurasi detik.
POLL_SECONDS = 0.01

Runner = Callable[["AgentNode"], str]


@dataclass
class AgentNode:
    node_id: str
    agent_id: str
    parent_id: str | None
    depth: int
    prompt: str
    status: str = PENDING
    result: str = ""
    messages: list[str] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def duration_s(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.time()
        return round(end - self.started_at, 3)

    def to_dict(self, *, include_prompt: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "node_id": self.node_id,
            "agent_id": self.agent_id,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "status": self.status,
            "result": self.result,
            "messages": list(self.messages),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
        }
        if include_prompt:
            data["prompt"] = self.prompt
        return data


class AgentGraph:
    """Graf agen: spawn/send/wait/tree/kill_all dengan batas keras.

    `runner` bisa diinjeksi supaya test tidak butuh jaringan maupun orchestrator.
    Default-nya memakai jalur `SubagentRunner` yang sudah ada, tetapi gagal
    dengan pesan (bukan exception) kalau orchestrator tidak lengkap.
    """

    def __init__(
        self,
        *,
        run_id: str | None = None,
        runner: Runner | None = None,
        orchestrator: Any = None,
        persist: bool = True,
    ) -> None:
        self.run_id = run_id or f"graph-{uuid.uuid4().hex[:12]}"
        self._runner = runner
        self._orchestrator = orchestrator
        self._persist_enabled = persist
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._nodes: dict[str, AgentNode] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._seq = 0
        self.created_at = time.time()

    # -- state ---------------------------------------------------------

    @property
    def node_count(self) -> int:
        with self._lock:
            return len(self._nodes)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def nodes(self) -> list[dict[str, Any]]:
        with self._lock:
            return [n.to_dict() for n in self._nodes.values()]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "updated_at": time.time(),
            "cancelled": self._cancel.is_set(),
            "nodes": [n.to_dict() for n in self._nodes.values()],
        }

    def _persist(self) -> None:
        if not self._persist_enabled:
            return
        try:
            from wth_ai.agents.graph_store import save_graph

            save_graph(self.run_id, self.snapshot())
        except OSError:
            # Disk penuh / path read-only tidak boleh menghentikan graf yang
            # sedang berjalan; state di memori tetap otoritatif.
            pass

    # -- API -----------------------------------------------------------

    def spawn(self, agent_id: str, prompt: str, *, parent_id: str | None = None) -> dict[str, Any]:
        """Buat satu node dan mulai worker-nya. Tidak pernah melempar exception."""
        if get_spec(agent_id) is None:
            return {"ok": False, "error": f"unknown agent `{agent_id}`"}
        with self._lock:
            if self._cancel.is_set():
                return {"ok": False, "error": "graph cancelled (kill switch)"}
            depth = 0
            if parent_id is not None:
                parent = self._nodes.get(parent_id)
                if parent is None:
                    return {"ok": False, "error": f"unknown parent node `{parent_id}`"}
                depth = parent.depth + 1
            if depth >= MAX_DEPTH:
                return {
                    "ok": False,
                    "error": f"depth limit reached (MAX_DEPTH={MAX_DEPTH})",
                }
            if len(self._nodes) >= MAX_NODES:
                return {
                    "ok": False,
                    "error": f"node limit reached (MAX_NODES={MAX_NODES})",
                }
            self._seq += 1
            node = AgentNode(
                node_id=f"{agent_id}-{self._seq}",
                agent_id=agent_id,
                parent_id=parent_id,
                depth=depth,
                prompt=prompt,
            )
            self._nodes[node.node_id] = node
            # daemon=True tetapi selalu di-join di `wait`/`kill_all`: runner
            # bisa saja memblokir, dan thread yatim tidak boleh menahan exit proses.
            thread = threading.Thread(
                target=self._run_node,
                args=(node,),
                name=f"agent-{node.node_id}",
                daemon=True,
            )
            self._threads[node.node_id] = thread
        self._persist()
        thread.start()
        return {"ok": True, "node_id": node.node_id, "depth": depth}

    def send(self, node_id: str, message: str) -> dict[str, Any]:
        """Catat pesan ke inbox node.

        Jujur soal batasnya: runner sudah berjalan sejak `spawn`, jadi pesan ini
        tidak diantar ke agen — ia tersimpan di snapshot (`messages`) supaya
        operator bisa melihat konteks tambahan yang dimaksudkan, dan runner yang
        diinjeksi (mis. test) bisa membacanya sendiri.
        """
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                return {"ok": False, "error": f"unknown node `{node_id}`"}
            node.messages.append(message)
        self._persist()
        return {"ok": True, "node_id": node_id, "messages": len(node.messages)}

    def wait(self, node_id: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
        """Tunggu satu node (atau semua node graph ini) sampai terminal/timeout."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        if node_id is not None:
            with self._lock:
                if node_id not in self._nodes:
                    return {"ok": False, "error": f"unknown node `{node_id}`"}
        expired: list[str] = []
        while True:
            if requested():
                self.kill_all("killswitch")
            with self._lock:
                targets = (
                    [self._nodes[node_id]] if node_id is not None else list(self._nodes.values())
                )
            if all(n.status in TERMINAL for n in targets):
                break
            if time.monotonic() >= deadline:
                expired = self._expire(targets)
                break
            time.sleep(POLL_SECONDS)

        self._join(targets)
        with self._lock:
            payload: dict[str, Any] = {
                "ok": True,
                "nodes": {n.node_id: n.status for n in targets},
                "timed_out": expired,
            }
            if node_id is not None:
                node = self._nodes[node_id]
                payload["node_id"] = node.node_id
                payload["status"] = node.status
                payload["result"] = node.result
        self._persist()
        return payload

    def tree(self) -> dict[str, Any]:
        """Snapshot bersarang: akar graph ini beserta anak-anaknya."""
        with self._lock:
            children: dict[str | None, list[AgentNode]] = {}
            for node in self._nodes.values():
                children.setdefault(node.parent_id, []).append(node)

            def build(node: AgentNode) -> dict[str, Any]:
                return {
                    "node_id": node.node_id,
                    "agent_id": node.agent_id,
                    "parent_id": node.parent_id,
                    "depth": node.depth,
                    "status": node.status,
                    "duration_s": node.duration_s,
                    "children": [build(c) for c in children.get(node.node_id, [])],
                }

            return {
                "run_id": self.run_id,
                "roots": [build(n) for n in children.get(None, [])],
                "node_count": len(self._nodes),
            }

    def kill_all(self, reason: str = "killswitch") -> list[str]:
        """Tandai semua node hidup sebagai `killed` dan set event cancel."""
        self._cancel.set()
        killed: list[str] = []
        with self._lock:
            for node in self._nodes.values():
                if node.status in (PENDING, RUNNING):
                    node.status = KILLED
                    node.result = node.result or f"killed: {reason}"
                    node.finished_at = time.time()
                    killed.append(node.node_id)
        self._persist()
        return killed

    # -- internal ------------------------------------------------------

    def _expire(self, targets: list[AgentNode]) -> list[str]:
        """Node yang belum terminal saat deadline ditandai `timeout` (terminal)."""
        expired: list[str] = []
        with self._lock:
            for node in targets:
                if node.status in (PENDING, RUNNING):
                    node.status = TIMEOUT
                    node.finished_at = time.time()
                    expired.append(node.node_id)
        return expired

    def _join(self, targets: list[AgentNode]) -> None:
        for node in targets:
            thread = self._threads.get(node.node_id)
            if thread is not None and thread.is_alive():
                # Batas kecil: node yang masih jalan tidak boleh menahan `wait`.
                thread.join(timeout=0.1)

    def _run_node(self, node: AgentNode) -> None:
        with self._lock:
            if node.status != PENDING:
                # Sudah dibunuh sebelum thread sempat mulai: jangan jalankan runner.
                return
            node.status = RUNNING
            node.started_at = time.time()
        self._persist()
        try:
            result = str(self._runner_for()(node))
            status = DONE
        except Exception as exc:  # noqa: BLE001 - satu node gagal bukan urusan induk
            result = f"{type(exc).__name__}: {exc}"
            status = ERROR
        with self._lock:
            node.result = result
            if node.status == RUNNING:
                node.status = KILLED if self._cancel.is_set() else status
            # status terminal lain (timeout/killed) tidak ditimpa; hasil tetap disimpan.
            node.finished_at = time.time()
        self._persist()

    def _runner_for(self) -> Runner:
        return self._runner or self._default_runner

    def _default_runner(self, node: AgentNode) -> str:
        """Jalur `SubagentRunner` yang sudah ada, tanpa memaksa jaringan.

        Orchestrator yang tidak lengkap (mis. di test atau saat TUI belum siap)
        harus menghasilkan pesan yang jujur, bukan AttributeError yang menutupi
        penyebabnya.
        """
        orch = self._orchestrator
        if orch is None:
            return f"HIPOTESIS (belum dibuktikan): node {node.agent_id} tidak dijalankan, orchestrator tidak tersedia."
        if not hasattr(orch, "state"):
            return f"HIPOTESIS (belum dibuktikan): node {node.agent_id} tidak dijalankan, orchestrator tanpa `state`."
        from wth_ai.agents.subagent.runner import SubagentRunner

        try:
            runner = SubagentRunner(orch, depth=node.depth)
            return runner.run(node.agent_id, node.prompt)
        except AttributeError as exc:
            return f"ERROR: orchestrator tidak lengkap ({exc}); node {node.agent_id} tidak dijalankan."
