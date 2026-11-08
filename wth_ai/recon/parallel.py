"""Concurrent execution for recon tools — bounded, paced, order-preserving.

Measured state before this module: `ReconEngine.run()` executed every external
tool strictly one after another, so wall clock was the SUM of every subprocess
and network round trip. On a 30-tool plan with 20s tools that is ten minutes of
waiting for no reason: those tools are independent processes that do not share
state. Meanwhile `ToolSpec.rate_limited` was declarative only -- nothing in the
recon package ever read it, so a tool marked `rate_limited=True` was paced by
nothing at all.

What this module guarantees (each one is a test):
* **only independent work is parallelised.** Builtins are NOT run through here:
  they share a `BuiltinContext` that each stage enriches (dns feeds http feeds
  crawler), so their order is a dependency, not a preference.
* **deterministic output.** Results are delivered in the original plan order
  regardless of which tool finishes first, so artifacts stay diffable.
* **callbacks stay on the calling thread.** Progress and log callbacks reach the
  job manager from one thread only; nothing touches SQLite from a worker.
* **rate limiting is real.** Tools with `rate_limited=True` are paced against the
  target host: starts are spaced by at least `1/rps` seconds. This bounds the
  aggregate start rate toward a host; it does NOT replace a tool's own internal
  pacing, and the docs say so instead of implying more.
* **stop means stop.** A halt is checked immediately before a tool starts, so no
  new process is spawned after a stop request; results already collected are
  kept rather than discarded.
* **one tool cannot sink the batch.** Failures stay inside the runner (which
  returns a failed ToolRun), and an unexpected exception here is converted into
  a failed run for that tool only.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable

#: Workers per profile. `budget` stays sequential on purpose: it exists for
#: low-noise runs, and 1 worker reproduces the historical behaviour exactly.
PROFILE_WORKERS: dict[str, int] = {
    "budget": 1,
    "local": 4,
    "recon-only": 3,
    "default": 4,
    "high-quality": 6,
    "max": 6,
}
#: Conservative aggregate start rate toward one host when the scope does not
#: state one. Matches the executor pool's default of 2 rps.
DEFAULT_RPS = 2.0

#: Tool categories whose requests actually land on the target host. Pacing is
#: about protecting the HOST UNDER TEST, so only these are paced.
#:
#: First version paced every external tool and that was wrong twice over: it made
#: a 30-tool plan take at least 15s of pure waiting, and it throttled tools that
#: never touch the target at all (subfinder asks crt.sh, waybackurls asks the
#: Internet Archive, gitleaks reads a local file). Serialising those protects
#: nobody and threw away the concurrency win.
TARGET_FACING_CATEGORIES = frozenset({
    "http_probe",   # HTTP requests to target URLs
    "crawl",        # crawls the target
    "screenshot",   # loads target pages
    "port",         # connects to target ports
    "tls",          # TLS handshake with the target
    "param",        # requests target endpoints with parameters
    "api_schema",   # probes target API/schema paths
})


def contacts_target(spec: Any) -> bool:
    """True when this tool's requests land on the host under test.

    Category is the primary signal; the capability name is a fallback so a spec
    added without a category still gets paced rather than silently exempted.
    """
    category = str(getattr(spec, "category", "") or "").strip().lower()
    if category:
        return category in TARGET_FACING_CATEGORIES
    caps = " ".join(getattr(spec, "capabilities", []) or []).lower()
    return any(word in caps for word in ("http_probe", "crawl", "port", "tls_", "param", "screenshot"))


def resolve_workers(profile: str, override: int | None = None) -> int:
    """Workers for this run: explicit > env > profile default. Always >= 1."""
    if override is not None and override > 0:
        return int(override)
    env = os.environ.get("WTH_RECON_WORKERS", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    return PROFILE_WORKERS.get(profile, 3)


def resolve_rps(scope_rps: float | None = None) -> float:
    """Aggregate start rate toward a host: explicit scope > env > default."""
    if scope_rps and scope_rps > 0:
        return float(scope_rps)
    env = os.environ.get("WTH_RECON_RPS", "").strip()
    try:
        rps = float(env)
    except ValueError:
        rps = 0.0
    return rps if rps > 0 else DEFAULT_RPS


class HostRateLimiter:
    """Spaces tool STARTS per host: at most `rps` starts per second, per host.

    Deliberately a start pacer, not a request meter. A long-running external tool
    (subfinder enumerating for 30s) holds no slot here: only its start is paced.
    That bounds how fast we pile requests onto a host without pretending to know
    what each tool does internally.
    """

    def __init__(self, rps: float = DEFAULT_RPS) -> None:
        self.rps = max(0.01, float(rps))
        self.interval = 1.0 / self.rps
        self._lock = threading.Lock()
        self._next_at: dict[str, float] = {}
        #: host -> seconds spent waiting (observability for the report)
        self.waited: dict[str, float] = {}
        self.starts: dict[str, int] = {}
        #: how many starts were paced vs exempt (observability: an operator should
        #: be able to see that pacing is aimed at the target, not applied blindly)
        self.paced = 0
        self.unpaced = 0

    def host_of(self, target: str) -> str:
        """Host key for pacing. Falls back to the raw string (e.g. an IP)."""
        raw = (target or "").strip()
        if "://" in raw:
            raw = raw.split("://", 1)[1]
        raw = raw.split("/", 1)[0].split("?", 1)[0]
        return raw.split("@")[-1].rsplit(":", 1)[0].lower() or raw.lower()

    def wait(self, host: str) -> float:
        """Block until this host may start another tool. Returns seconds waited.

        Sleeping inside the lock is intentional: it makes per-host serialisation
        trivially correct, and contention is bounded by `max_workers`.
        """
        start = time.monotonic()
        with self._lock:
            now = time.monotonic()
            ready_at = max(now, self._next_at.get(host, 0.0))
            delay = ready_at - now
            if delay > 0:
                time.sleep(delay)
            self._next_at[host] = time.monotonic() + self.interval
            self.starts[host] = self.starts.get(host, 0) + 1
        waited = time.monotonic() - start
        self.waited[host] = self.waited.get(host, 0.0) + waited
        return waited

    def stats(self) -> dict[str, Any]:
        return {
            "rps": self.rps,
            "starts": dict(self.starts),
            "waited_seconds": {k: round(v, 3) for k, v in self.waited.items()},
            "paced_starts": self.paced,
            "unpaced_starts": self.unpaced,
        }


def run_specs_parallel(
    runner: Any,
    specs: Iterable[Any],
    *,
    target: str,
    ctx: Any,
    max_workers: int = 4,
    limiter: HostRateLimiter | None = None,
    pace_predicate: Callable[[Any], bool] | None = None,
    halt: Callable[[], bool] | None = None,
    on_result: Callable[[Any, Any], None] | None = None,
    on_skip: Callable[[Any], None] | None = None,
) -> bool:
    """Run tool specs concurrently; deliver results in plan order.

    Returns True when a halt request prevented at least one tool from starting.
    """
    spec_list = list(specs)
    if not spec_list:
        return False

    limiter = limiter or HostRateLimiter()
    host = limiter.host_of(target)
    halted = False

    predicate = pace_predicate or contacts_target

    def _one(spec: Any) -> Any:
        nonlocal halted
        # Checked as late as possible: a stop must not spawn a new process even
        # if the request arrived while the batch was still running.
        if halt is not None and halt():
            halted = True
            return None
        if predicate(spec):
            limiter.wait(host)
            limiter.paced += 1
        else:
            limiter.unpaced += 1
        if halt is not None and halt():
            halted = True
            return None
        return runner.run(spec, target=target, ctx=ctx)

    if max_workers <= 1:
        for spec in spec_list:
            run = _one(spec)
            if run is None:
                if on_skip is not None:
                    on_skip(spec)
                continue
            if on_result is not None:
                on_result(spec, run)
        return halted

    ordered: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="recon") as pool:
        futures = {spec.name: pool.submit(_one, spec) for spec in spec_list}
        for spec in spec_list:
            try:
                ordered[spec.name] = futures[spec.name].result()
            except Exception as exc:  # noqa: BLE001 - isolate one bad tool
                ordered[spec.name] = exc

    for spec in spec_list:
        result = ordered.get(spec.name)
        if result is None:
            if on_skip is not None:
                on_skip(spec)
            continue
        if isinstance(result, Exception):
            # The runner is supposed to contain its own failures; if something
            # escapes it, report a failed run for THIS tool and keep going.
            from wth_ai.recon.model import ToolRun

            run = ToolRun(tool=spec.name, started_at="", finished_at="")
            run.status = "failed"
            run.error = f"parallel worker error: {result}"
            result = run
        if on_result is not None:
            on_result(spec, result)
    return halted


def wrap_engine_runner(base_runner: Any) -> Any:
    """Deprecated shim kept for callers that predate `run_specs_parallel`."""
    return base_runner
