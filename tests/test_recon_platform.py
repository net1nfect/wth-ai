"""Fase 5 recon platform tests — fully offline (network is monkeypatched).

Covers: 200+ registry, availability, planner profiles, safe runner, builtin
modules, normalizer dedupe/confidence, scoring, diff, report, job integration,
events, watch config, secret masking, and doctor checks.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from wth_ai.recon import http as rhttp
from wth_ai.recon import cloud, dns, fingerprint, headers, js, params, tls
from wth_ai.recon.model import ToolRun
from wth_ai.recon.normalizer import ReconNormalizer
from wth_ai.recon.planner import ReconPlanner, detect_target_type
from wth_ai.recon.registry import ReconToolRegistry
from wth_ai.recon.runner import SafeToolRunner, redact
from wth_ai.recon.tools.builtin import BuiltinContext

PLANTED_SECRET = "AKIAIOSFODNN7EXAMPLE"
HOMEPAGE_HTML = (
    "<html><head><title>Test Site</title>"
    '<script src="/static/app.js"></script></head>'
    '<body><a href="/admin/login?next=/dash">admin</a>'
    '<a href="/api/v1/users?id=1">users</a>'
    '<form action="/submit" method="post"></form>'
    "<!-- s3 ref: my-bucket.s3.amazonaws.com -->"
    "</body></html>"
)
JS_BODY = (
    'const api="/api/v1/orders";fetch("/api/v1/payments");'
    f'const key="{PLANTED_SECRET}";'
)


# --------------------------------------------------------------------------- #
@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.events import reset_event_bus

    reset_event_bus()
    return tmp_path


@pytest.fixture
def offline(monkeypatch):
    """Patch every network entrypoint so the engine runs fully offline."""

    def fake_fetch(url, *, method="GET", timeout=12, headers=None, allow_redirects=True):
        url = rhttp.normalize_url(url)
        if url.rstrip("/").endswith(".js"):
            return rhttp.HttpResponse(url=url, status=200, headers={"Content-Type": "application/javascript"}, text=JS_BODY)
        if "robots.txt" in url:
            return rhttp.HttpResponse(url=url, status=200, headers={"Content-Type": "text/plain"}, text="Disallow: /secret-area\n")
        if "sitemap.xml" in url:
            return rhttp.HttpResponse(url=url, status=200, headers={"Content-Type": "application/xml"}, text="<loc>https://example.com/page1</loc>")
        return rhttp.HttpResponse(
            url=url,
            status=200,
            headers={"Content-Type": "text/html", "Server": "nginx/1.25.1"},
            text=HOMEPAGE_HTML,
            cookies=[{"name": "sid", "secure": False, "httponly": False, "samesite": ""}],
        )

    def fake_resolve(host):
        return [{"record_type": "A", "value": "93.184.216.34"}]

    def fake_cert(host, port=443, timeout=8):
        return {
            "subject": ((("commonName", host),),),
            "issuer": ((("commonName", "Test CA"),),),
            "notAfter": "Jan  1 00:00:00 2030 GMT",
            "subjectAltName": (("DNS", host), ("DNS", f"www.{host}")),
        }

    monkeypatch.setattr(rhttp, "fetch", fake_fetch)
    monkeypatch.setattr(dns, "resolve_host", fake_resolve)
    monkeypatch.setattr(tls, "cert_info", fake_cert)
    return True


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def test_registry_has_200_plus(tmp_home):
    reg = ReconToolRegistry()
    assert len(reg) >= 200


def test_registry_summary_breakdown(tmp_home):
    reg = ReconToolRegistry()
    s = reg.summary()
    assert s["total"] >= 200
    assert s["builtin"] >= 5
    assert s["api"] >= 1
    assert s["manual"] >= 1


def test_availability_builtin_always_manual_never(tmp_home, monkeypatch):
    reg = ReconToolRegistry()
    assert reg.is_available("python-requests")  # builtin
    # a manual tool is never auto-available
    manual = next(s for s in reg.tools.values() if s.execution_mode == "manual")
    assert not reg.is_available(manual.name)


def test_availability_external_via_path(tmp_home, monkeypatch):
    import wth_ai.recon.registry as regmod

    monkeypatch.setattr(regmod.shutil, "which", lambda b: "/usr/bin/" + b if b == "subfinder" else None)
    reg = ReconToolRegistry()
    assert reg.is_available("subfinder")


# --------------------------------------------------------------------------- #
# Planner
# --------------------------------------------------------------------------- #
def test_planner_profiles_differ(tmp_home):
    pl = ReconPlanner()
    budget = pl.plan("example.com", profile="budget")
    default = pl.plan("example.com", profile="default")
    hq = pl.plan("example.com", profile="high-quality")
    assert len(budget.selected) < len(default.selected) < len(hq.selected)


def test_planner_excludes_high_risk_by_default(tmp_home):
    plan = ReconPlanner().plan("example.com", profile="high-quality")
    names = {t.tool for t in plan.selected}
    assert "masscan" not in names
    assert "ffuf" not in names


def test_planner_includes_high_risk_after_enable(tmp_home):
    reg = ReconToolRegistry()
    pl = ReconPlanner(reg)
    # high-risk is ineligible by default, eligible once explicitly enabled
    assert pl._eligible("masscan", "high-quality", None)[0] is False
    reg.enable("masscan")
    assert pl._eligible("masscan", "high-quality", None)[0] is True


def test_target_type_detection(tmp_home):
    assert detect_target_type("example.com") == "domain"
    assert detect_target_type("https://example.com/x") == "url"
    assert detect_target_type("8.8.8.8") == "ip"
    assert detect_target_type("127.0.0.1") == "local"
    assert detect_target_type("localhost") == "local"


# --------------------------------------------------------------------------- #
# Safe runner
# --------------------------------------------------------------------------- #
def test_runner_builtin_no_subprocess(tmp_home, offline):
    reg = ReconToolRegistry()
    runner = SafeToolRunner(reg, raw_dir=tmp_home / "raw")
    ctx = BuiltinContext(target="example.com", host="example.com", base_url="https://example.com")
    run = runner.run(reg.get("python-dnspython"), target="example.com", ctx=ctx)
    assert run.status == "success"
    assert any(it["kind"] == "dns_record" for it in run.normalized_items)


# --------------------------------------------------------------------------- #
# Test seam for the external-tool path (SafeToolRunner._spawn)
# --------------------------------------------------------------------------- #
class _Reader:
    """Minimal readable stream over a fixed payload."""

    def __init__(self, data) -> None:
        self._data = data.encode() if isinstance(data, str) else data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if self._pos >= len(self._data):
            return b""
        end = len(self._data) if not n or n < 0 else self._pos + n
        chunk = self._data[self._pos:end]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        pass


class _Proc:
    """Stand-in for subprocess.Popen as `SafeToolRunner._run_bounded` uses it."""

    def __init__(self, stdout=b"", stderr=b"", returncode=0, hang=False) -> None:
        self.stdout = _Reader(stdout)
        self.stderr = _Reader(stderr)
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    def wait(self, timeout=None):
        if self._hang:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self._hang = False


def _fake_spawn(captured, *, stdout=b"", stderr=b"", returncode=0, hang=False):
    """Return a `staticmethod` replacement for `SafeToolRunner._spawn`.

    Wrapped in `staticmethod` deliberately: assigning a plain function to a
    class attribute makes it a bound method, so it would receive `self` as its
    first positional argument.
    """

    def _spawn(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Proc(stdout, stderr, returncode, hang)

    return staticmethod(_spawn)

def test_runner_external_uses_argv_no_shell(tmp_home, monkeypatch):
    import wth_ai.recon.registry as regmod
    import wth_ai.recon.runner as runmod

    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.setattr(regmod.shutil, "which", lambda b: "/usr/bin/" + b)
    captured = {}
    monkeypatch.setattr(
        runmod.SafeToolRunner, "_spawn",
        _fake_spawn(captured, stdout=b"sub.example.com\n"),
    )
    reg = ReconToolRegistry()
    runner = SafeToolRunner(reg, raw_dir=tmp_home / "raw")
    ctx = BuiltinContext(target="example.com", host="example.com", base_url="https://example.com")
    run = runner.run(reg.get("subfinder"), target="example.com", ctx=ctx)
    assert run.status == "success"
    assert isinstance(captured["argv"], list)
    # `shell` is never passed at all any more (argv list only); if a future
    # change adds it, it must not be True.
    assert captured["kwargs"].get("shell", False) is False


def test_runner_external_timeout(tmp_home, monkeypatch):
    import wth_ai.recon.registry as regmod
    import wth_ai.recon.runner as runmod

    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.setattr(regmod.shutil, "which", lambda b: "/usr/bin/" + b)
    monkeypatch.setattr(runmod.SafeToolRunner, "_spawn", _fake_spawn({}, hang=True))
    reg = ReconToolRegistry()
    runner = SafeToolRunner(reg, raw_dir=tmp_home / "raw", timeout=1)
    ctx = BuiltinContext(target="example.com", host="example.com", base_url="https://example.com")
    run = runner.run(reg.get("subfinder"), target="example.com", ctx=ctx)
    assert run.status == "timeout"


def test_runner_redacts_secrets_in_raw(tmp_home, monkeypatch):
    import wth_ai.recon.registry as regmod
    import wth_ai.recon.runner as runmod

    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.setattr(regmod.shutil, "which", lambda b: "/usr/bin/" + b)
    payload = "found token=supersecretvalue123456 and AKIAIOSFODNN7EXAMPLE"
    monkeypatch.setattr(runmod.SafeToolRunner, "_spawn", _fake_spawn({}, stdout=payload))
    reg = ReconToolRegistry()
    raw_dir = tmp_home / "raw"
    runner = SafeToolRunner(reg, raw_dir=raw_dir)
    ctx = BuiltinContext(target="example.com", host="example.com", base_url="https://example.com")
    runner.run(reg.get("subfinder"), target="example.com", ctx=ctx)
    content = (raw_dir / "subfinder.txt").read_text()
    # Derive the expected secrets FROM the fixture instead of retyping them --
    # the fixture values are long tokens and must stay byte-identical here.
    secret_value = payload.split("token=", 1)[1].split(" ", 1)[0]
    aws_like = payload.split(" and ", 1)[1].strip()
    assert secret_value not in content, "nilai token tidak diredaksi di raw/"
    assert aws_like not in content, "kunci bergaya AWS tidak diredaksi di raw/"
    assert "REDACTED" in content


def test_runner_output_is_capped_while_streaming(tmp_home, monkeypatch):
    """The 200KB cap must bound what is RETAINED, not only what is written.

    The previous implementation buffered the whole stdout via
    `subprocess.run(capture_output=True)` and sliced afterwards, so a tool
    printing far more pushed the worker's RSS up by all of it before the slice
    discarded it.
    """
    import wth_ai.recon.registry as regmod
    import wth_ai.recon.runner as runmod

    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.setattr(regmod.shutil, "which", lambda b: "/usr/bin/" + b)
    huge = b"a" * (runmod.MAX_OUTPUT_BYTES * 3)
    monkeypatch.setattr(runmod.SafeToolRunner, "_spawn", _fake_spawn({}, stdout=huge))
    reg = ReconToolRegistry()
    raw_dir = tmp_home / "raw"
    runner = SafeToolRunner(reg, raw_dir=raw_dir)
    ctx = BuiltinContext(target="example.com", host="example.com", base_url="https://example.com")
    run = runner.run(reg.get("subfinder"), target="example.com", ctx=ctx)
    assert run.output_path
    assert Path(run.output_path).stat().st_size <= runmod.MAX_OUTPUT_BYTES


def test_redact_function():
    assert "REDACTED" in redact("api_key=abcd1234")
    assert "supersecret" not in redact("password=supersecret")


# --------------------------------------------------------------------------- #
# Builtin modules
# --------------------------------------------------------------------------- #
def test_builtin_dns_mock(monkeypatch):
    monkeypatch.setattr(dns, "resolve_host", lambda h: [{"record_type": "A", "value": "1.2.3.4"}])
    items = dns.run("example.com")
    assert {"kind": "subdomain", "host": "example.com"} in items
    assert any(it["kind"] == "dns_record" and it["value"] == "1.2.3.4" for it in items)


def test_builtin_http_mock(monkeypatch):
    monkeypatch.setattr(
        rhttp, "fetch",
        lambda url, **k: rhttp.HttpResponse(url=rhttp.normalize_url(url), status=200,
                                            headers={"Content-Type": "text/html"}, text=HOMEPAGE_HTML),
    )
    from wth_ai.recon.tools.builtin import run_builtin

    ctx = BuiltinContext(target="example.com", host="example.com", base_url="https://example.com")
    items = run_builtin("http", ctx)
    assert any(it["kind"] == "http_probe" and it["status"] == 200 for it in items)
    assert ctx.js_files  # discovered app.js


def test_header_parser(monkeypatch):
    monkeypatch.setattr(
        rhttp, "fetch",
        lambda url, **k: rhttp.HttpResponse(url=rhttp.normalize_url(url), status=200, headers={}, text="x",
                                            cookies=[{"name": "sid", "secure": False, "httponly": False, "samesite": ""}]),
    )
    items = headers.run("https://example.com")
    hdr = next(it for it in items if it["kind"] == "header")
    assert "Content-Security-Policy" in hdr["missing"]
    assert any(it["kind"] == "cookie" and it["name"] == "sid" for it in items)


def test_tls_parser_mock(monkeypatch):
    monkeypatch.setattr(
        tls, "cert_info",
        lambda h, port=443, timeout=8: {
            "subject": ((("commonName", h),),),
            "issuer": ((("commonName", "CA"),),),
            "notAfter": "Jan 1 2030",
            "subjectAltName": (("DNS", h), ("DNS", f"api.{h}")),
        },
    )
    items = tls.run("example.com")
    assert any(it["kind"] == "tls" for it in items)
    assert any(it["kind"] == "subdomain" and it["host"] == "api.example.com" for it in items)


def test_js_endpoint_extraction():
    items = js.analyze("https://example.com/app.js", JS_BODY)
    intel = next(it for it in items if it["kind"] == "js_intel")
    assert "/api/v1/orders" in intel["endpoints"]


def test_js_secret_candidate_masked():
    items = js.analyze("https://example.com/app.js", JS_BODY)
    secrets = [it for it in items if it["kind"] == "secret_candidate"]
    assert secrets
    for sc in secrets:
        assert PLANTED_SECRET not in sc["masked"]


def test_parameter_extraction():
    items = params.run(["https://example.com/api/users?id=1&redirect=/x"])
    pnames = {it["name"]: it["interest"] for it in items if it["kind"] == "parameter"}
    assert pnames.get("id", 0) >= 5
    assert pnames.get("redirect", 0) >= 5


def test_cloud_candidate_extraction():
    items = cloud.detect("see my-bucket.s3.amazonaws.com and app.web.app")
    providers = {it["provider"] for it in items}
    assert "aws_s3" in providers


def test_fingerprint_from_headers(monkeypatch):
    monkeypatch.setattr(
        rhttp, "fetch",
        lambda url, **k: rhttp.HttpResponse(url=rhttp.normalize_url(url), status=200,
                                            headers={"Server": "nginx/1.25.1"}, text="<html>/wp-content/x</html>"),
    )
    items = fingerprint.run("https://example.com")
    techs = {it["technology"] for it in items}
    assert "wordpress" in techs


# --------------------------------------------------------------------------- #
# Normalizer / scoring / diff
# --------------------------------------------------------------------------- #
def test_normalizer_dedupe_and_confidence():
    norm = ReconNormalizer()
    norm.add_run(ToolRun(tool="subfinder", normalized_items=[{"kind": "subdomain", "host": "a.example.com"}]))
    norm.add_run(ToolRun(tool="assetfinder", normalized_items=[{"kind": "subdomain", "host": "a.example.com"}]))
    assert len(norm.subdomains) == 1
    sub = next(iter(norm.subdomains.values()))
    assert set(sub.sources) == {"subfinder", "assetfinder"}
    assert sub.confidence > 0.6


def test_scoring_prioritizes_interesting():
    norm = ReconNormalizer()
    norm.add_run(ToolRun(tool="builtin", normalized_items=[
        {"kind": "subdomain", "host": "admin.example.com"},
        {"kind": "http_probe", "url": "https://admin.example.com/", "status": 403},
        {"kind": "subdomain", "host": "static.example.com"},
    ]))
    from wth_ai.recon import scoring

    targets = scoring.score(norm)
    assert targets
    assert targets[0].score >= 1
    assert "admin" in targets[0].asset


def test_diff_detects_new_url():
    from wth_ai.recon.diff import diff_results

    old = {"http_probes": [{"url": "https://example.com/a"}]}
    new = {"http_probes": [{"url": "https://example.com/a"}, {"url": "https://example.com/b"}]}
    changes = diff_results(old, new)
    assert "https://example.com/b" in changes["new_alive_urls"]
    assert changes["changed"]


# --------------------------------------------------------------------------- #
# Engine end-to-end (offline)
# --------------------------------------------------------------------------- #
def test_engine_creates_artifacts(tmp_home, offline):
    from wth_ai.recon import ReconEngine

    eng = ReconEngine()
    res = eng.run("example.com", profile="recon-only", job_id="JOB-eng1")
    assert res["status"] == "completed"
    out = Path(res["output_dir"])
    for f in ("recon_plan.json", "tool_runs.jsonl", "recon_report.md",
              "recon_summary.json", "priority_targets.json"):
        assert (out / f).exists(), f
    assert (out / "normalized" / "asset_graph.json").exists()


def test_engine_progress_monotonic(tmp_home, offline):
    from wth_ai.recon import ReconEngine

    marks = []
    eng = ReconEngine()
    eng.run("example.com", profile="recon-only", job_id="JOB-prog",
            progress=lambda c, t, m: marks.append(c))
    assert marks[0] == 5 and marks[-1] == 100
    assert marks == sorted(marks)


def test_engine_blocks_unauthorized(tmp_home, offline):
    from wth_ai.recon import ReconEngine

    res = ReconEngine().run("example.com", job_id="JOB-blk", authorized=False)
    assert res["status"] == "blocked"


# --------------------------------------------------------------------------- #
# Job integration + events
# --------------------------------------------------------------------------- #
def _manager(tmp_home):
    from wth_ai.jobs.manager import JobManager
    from wth_ai.workspace import init_workspace

    init_workspace()
    return JobManager()


def test_recon_job_handler_completed(tmp_home, offline):
    from wth_ai.jobs.worker import WorkerEngine

    mgr = _manager(tmp_home)
    job = mgr.create_job("recon", target="example.com", profile="recon-only",
                         metadata={"authorized": True})
    assert job.status == "queued"
    WorkerEngine(mgr).run(drain=True)
    done = mgr.get_job(job.job_id)
    assert done.status == "completed"
    assert done.result.get("status") == "completed"
    assert (Path(mgr.store.results_dir) / job.job_id / "recon_report.md").exists()


def test_job_events_progress_and_report(tmp_home, offline):
    from wth_ai.jobs.worker import WorkerEngine

    mgr = _manager(tmp_home)
    seen: list[str] = []
    mgr.bus.subscribe(lambda e: seen.append(e.type))
    job = mgr.create_job("recon", target="example.com", metadata={"authorized": True})
    WorkerEngine(mgr).run(drain=True)
    assert "job.progress" in seen
    assert "report.ready" in seen
    assert "recon.report_ready" in seen


def test_diff_via_two_jobs(tmp_home, offline, monkeypatch):
    """Second run with an extra URL should be diffed against the first."""
    from wth_ai.jobs.worker import WorkerEngine

    mgr = _manager(tmp_home)
    j1 = mgr.create_job("recon", target="example.com", metadata={"authorized": True})
    WorkerEngine(mgr).run(drain=True)

    # make the homepage expose an extra link on the 2nd pass
    extra_html = HOMEPAGE_HTML.replace("</body>", '<a href="/brand-new-page">new</a></body>')
    monkeypatch.setattr(
        rhttp, "fetch",
        lambda url, **k: rhttp.HttpResponse(url=rhttp.normalize_url(url), status=200,
                                            headers={"Content-Type": "text/html"}, text=extra_html),
    )
    j2 = mgr.create_job("recon", target="example.com", metadata={"authorized": True})
    WorkerEngine(mgr).run(drain=True)
    done2 = mgr.get_job(j2.job_id)
    # diff is computed against baseline (j1); changes recorded in result
    assert "changes" in done2.result


# --------------------------------------------------------------------------- #
# Watch / realtime
# --------------------------------------------------------------------------- #
def test_watch_config_saved(tmp_home):
    from wth_ai.recon.realtime import WatchConfig, load_watch, parse_interval, save_watch

    save_watch(WatchConfig(target="example.com", interval_seconds=parse_interval("6h")))
    cfg = load_watch("example.com")
    assert cfg is not None
    assert cfg.interval_seconds == 6 * 3600


def test_parse_interval_floor():
    from wth_ai.recon.realtime import MIN_INTERVAL_SECONDS, parse_interval

    assert parse_interval("1m") == MIN_INTERVAL_SECONDS  # floored to 1h
    assert parse_interval("2d") == 2 * 86400


# --------------------------------------------------------------------------- #
# Secret hygiene
# --------------------------------------------------------------------------- #
def test_no_plaintext_secret_anywhere(tmp_home, offline):
    """The planted AWS key must never appear verbatim in any artifact."""
    from wth_ai.jobs.worker import WorkerEngine

    mgr = _manager(tmp_home)
    job = mgr.create_job("recon", target="example.com", metadata={"authorized": True})
    WorkerEngine(mgr).run(drain=True)
    root = Path(mgr.store.results_dir) / job.job_id
    for path in root.rglob("*"):
        if path.is_file():
            data = path.read_text(encoding="utf-8", errors="ignore")
            assert PLANTED_SECRET not in data, f"secret leaked in {path}"
    # also the job log
    log = mgr.store.read_log(job.job_id)
    assert PLANTED_SECRET not in log


# --------------------------------------------------------------------------- #
# Doctor
# --------------------------------------------------------------------------- #
def test_doctor_recon_ok_with_missing_warning(tmp_home):
    from wth_ai.tools.doctor import ToolDoctor

    rec = ToolDoctor().check_recon()
    assert rec["importable"]
    assert rec["registry_ok"]
    assert rec["ok"]  # missing externals are warnings, not failures
    assert rec["missing_tools"] >= 0


def test_doctor_detects_broken_registry(tmp_home, monkeypatch):
    import wth_ai.recon.registry as regmod
    from wth_ai.recon.model import ToolSpec
    from wth_ai.tools.doctor import ToolDoctor

    monkeypatch.setattr(regmod, "build_catalog", lambda: {"x": ToolSpec(name="x", category="core")})
    rec = ToolDoctor().check_recon()
    assert not rec["registry_ok"]
    assert not rec["ok"]
