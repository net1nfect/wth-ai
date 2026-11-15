"""Pipeline probe: surface, baseline, diff, eksekusi POST JSON, verdict."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from wth_ai.models import ExecutorTask, PermissionMode, SessionState
from wth_ai.pipeline.models import ProbePlan, RequestSpec, Surface, TechniqueResult
from wth_ai.pipeline.runner import run_technique
from wth_ai.pipeline.session import ProbeSession
from wth_ai.pipeline.techniques import get_technique
from wth_ai.executor.vuln.sqli import SqliExecutor


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/sqli":
            value = parse_qs(parsed.query).get("id", [""])[0]
            if "'" in value:
                self._send(500, b"you have an error in your sql syntax")
            else:
                self._send(200, b"ok " + b"x" * 200)
        else:
            self._send(404, b"not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        if parsed.path == "/api/login":
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._send(400, b"bad json", "application/json")
                return
            if "'" in str(data.get("user", "")):
                self._send(500, b"SQL syntax error near quote", "application/json")
            else:
                self._send(200, b'{"ok":true}', "application/json")
        else:
            self._send(404, b"not found")


def _task(url: str, **kwargs) -> ExecutorTask:
    return ExecutorTask(id="t1", goal="probe", target=url,
                        permission_required=PermissionMode.STANDARD_TESTING, **kwargs)


def _state() -> SessionState:
    state = SessionState()
    state.scope.target = "127.0.0.1"
    state.scope.in_scope = ["127.0.0.1"]
    state.scope.authorized = True
    state.scope.rate_limit_rps = 100.0
    return state


def test_surface_extracts_all_query_params():
    surface = Surface.from_target("http://target.test/a?id=1&next=/home")
    assert [(p.name, p.location) for p in surface.params] == [("id", "query"), ("next", "query")]
    changed = surface.request_for(surface.params[0], "1'")
    assert "id=1%27" in changed.url
    assert "next=%2Fhome" in changed.url


def test_surface_extracts_json_body_params():
    surface = Surface.from_target(
        "http://target.test/api/login", method="POST",
        headers={"Content-Type": "application/json"},
        body='{"user":"admin","pass":"x"}',
    )
    assert [(p.name, p.location) for p in surface.params] == [("user", "json"), ("pass", "json")]
    probe = surface.request_for(surface.params[0], "admin'")
    assert '"user": "admin\'"' in probe.body
    assert probe.headers["Content-Type"] == "application/json"


def test_probe_session_keeps_baseline_and_diff_with_fake_sender():
    calls = []

    def sender(request):
        calls.append(request.url)
        body = "baseline" if len(calls) == 1 else "changed-body"
        return {"method": request.method, "url": request.url, "status_code": 200,
                "stdout": body, "response_headers": {"Content-Type": "text/html"}}

    surface = Surface.from_target("http://target.test/x?id=1")
    session = ProbeSession(sender=sender)
    baseline, body = session.baseline(surface)
    plan = get_technique("xss").plans(surface, baseline, body)[0]
    outcome = session.probe(plan, baseline)
    assert baseline.role == "baseline"
    assert outcome.plan.role.startswith("probe:")
    assert "body fingerprint berbeda" in outcome.diff
    assert len(calls) == 2


def test_sqli_pipeline_finds_json_body_error():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        result = SqliExecutor(_state()).execute(_task(
            f"{base}/api/login", method="POST",
            headers={"Content-Type": "application/json"},
            body='{"user":"admin","pass":"x"}',
        ))
        assert result.status == "success"
        assert any("error" in e.lower() for e in result.evidence)
    finally:
        httpd.shutdown()


def test_pipeline_no_params_is_informational():
    result = SqliExecutor(_state()).execute(_task("http://target.test/no-params"))
    assert result.status == "success"
    assert result.risk == "low"
    assert not result.evidence


def test_runner_enforces_probe_budget_and_records_skipped():
    plans = [
        ProbePlan(title=f"p{i}", request=RequestSpec("GET", f"http://target.test/x?id={i}"))
        for i in range(5)
    ]

    class _Fake:
        vuln_class = "xss"
        requires_parameter = True
        probe_budget = 12

        def plans(self, surface, baseline, baseline_body, *, context=None):
            return plans

        def interpret(self, surface, baseline, baseline_body, outcomes, *, context=None):
            return TechniqueResult(observations=[f"n={len(outcomes)}"])

        def early_verified(self, baseline, baseline_body, outcome):
            return False

    calls = []

    def sender(request):
        calls.append(request.url)
        return {"status_code": 200, "stdout": "ok", "response_headers": {}}

    surface = Surface.from_target("http://target.test/x?id=0")
    run = run_technique(_Fake(), surface, ProbeSession(sender=sender), max_probes=2)
    assert len(run.outcomes) == 2
    assert run.skipped == 3
    assert len(calls) == 3, "baseline + 2 probe"


def test_runner_stops_on_early_verified():
    plans = [
        ProbePlan(title=f"p{i}", request=RequestSpec("GET", f"http://target.test/x?id={i}"))
        for i in range(4)
    ]

    class _Fake:
        vuln_class = "xss"
        requires_parameter = True
        probe_budget = 12

        def plans(self, surface, baseline, baseline_body, *, context=None):
            return plans

        def interpret(self, surface, baseline, baseline_body, outcomes, *, context=None):
            return TechniqueResult(verified=True, evidence=["early"])

        def early_verified(self, baseline, baseline_body, outcome):
            return True

    calls = []

    def sender(request):
        calls.append(request.url)
        return {"status_code": 200, "stdout": "hit", "response_headers": {}}

    surface = Surface.from_target("http://target.test/x?id=0")
    run = run_technique(_Fake(), surface, ProbeSession(sender=sender))
    assert len(run.outcomes) == 1
    assert run.skipped == 3
    assert len(calls) == 2, "baseline + satu probe; sisa tidak dikirim"


def test_runner_does_not_probe_when_baseline_fails():
    calls = []

    def sender(request):
        calls.append(request.url)
        return {"status_code": 0, "stdout": "ERR:down", "response_headers": {}}

    class _Fake:
        vuln_class = "xss"
        requires_parameter = True
        probe_budget = 12

        def plans(self, surface, baseline, baseline_body, *, context=None):
            raise AssertionError("plans tidak boleh dipanggil saat baseline gagal")

        def interpret(self, surface, baseline, baseline_body, outcomes, *, context=None):
            return TechniqueResult()

        def early_verified(self, baseline, baseline_body, outcome):
            return False

    surface = Surface.from_target("http://target.test/x?id=1")
    run = run_technique(_Fake(), surface, ProbeSession(sender=sender))
    assert run.baseline.status == 0
    assert run.outcomes == []
    assert len(calls) == 1
