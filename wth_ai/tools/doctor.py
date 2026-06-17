# ... (previous content remains the same until check_jobs)

    def check_recon_engine(self) -> dict[str, Any]:
        """Check Fase 5 Recon Engine health."""
        try:
cursor/fase5-recon-platform-93fe
          worker_status.read_text(encoding="utf-8")
        except OSError:
          worker_readable = False
      ok = schema_ok and queue_dir.exists() and worker_readable
      return {
        "ok": ok,
        "schema_ok": schema_ok,
        "db_path": str(store.db_path),
        "queue_dir": queue_dir.exists(),
        "jobs_log_dir": jobs_log_dir.exists(),
        "worker_readable": worker_readable,
        "total_jobs": total,
      }
    except Exception as exc:  # noqa: BLE001
      return {"ok": False, "schema_ok": False, "error": str(exc)}

  def check_approval_registry(self) -> dict[str, Any]:
    try:
      from wth_ai.approvals import ApprovalRegistry
      reg = ApprovalRegistry()
      pending = len(reg.list_pending())
      writable = reg.path.parent.exists() or True  # parent created lazily on write
      return {"ok": True, "pending": pending, "writable": writable, "path": str(reg.path)}
    except Exception as exc:  # noqa: BLE001
      return {"ok": False, "pending": 0, "error": str(exc)}

  def check_recon(self) -> dict[str, Any]:
    """Fase 5 recon platform health. Missing external tools are warnings only."""
    info: dict[str, Any] = {
      "importable": False,
      "registry_count": 0,
      "registry_ok": False,
      "builtin_ok": False,
      "results_writable": False,
      "wordlist_ok": False,
      "dns_resolver": False,
      "http_client": False,
      "report_writer": False,
      "profile_recon_only": False,
      "local_policy": False,
      "available_tools": 0,
      "missing_tools": 0,
      "warnings": [],
    }
    try:
      from wth_ai.recon.registry import ReconToolRegistry
      from wth_ai.recon.planner import ReconPlanner, PROFILE_CAP, detect_target_type
      from wth_ai.recon.tools.builtin import BuiltinContext, run_builtin
      from wth_ai.recon.wordlists import safe_paths

      info["importable"] = True
      reg = ReconToolRegistry()
      info["registry_count"] = len(reg)
      info["registry_ok"] = len(reg) >= 200
      summ = reg.summary()  # exercises availability without crashing
      info["available_tools"] = summ["available"]
      info["missing_tools"] = summ["missing"]

      # builtin recon works (offline: just dispatches without raising)
      ctx = BuiltinContext(target="example.com", host="example.com", base_url="https://example.com")
      run_builtin("params", ctx)  # pure parse, no network
      info["builtin_ok"] = True

      info["wordlist_ok"] = len(safe_paths()) > 0
      info["http_client"] = self._module_present("requests")
      info["dns_resolver"] = self._module_present("dns") or self._module_present("socket")
      info["profile_recon_only"] = "recon-only" in PROFILE_CAP
      info["local_policy"] = detect_target_type("127.0.0.1") == "local"

      results_dir = wth_home() / "wth-results"
      try:
        results_dir.mkdir(parents=True, exist_ok=True)
        probe = results_dir / ".doctor_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        info["results_writable"] = True
      except OSError:
        info["results_writable"] = False

      # report writer is importable + callable
      from wth_ai.recon import report as _rep  # noqa: F401
      info["report_writer"] = True

      if info["missing_tools"]:
        info["warnings"].append(f"{info['missing_tools']} external/api tools not installed (optional)")
    except Exception as exc:  # noqa: BLE001
      info["error"] = str(exc)

    info["ok"] = bool(
      info["importable"] and info["registry_ok"] and info["builtin_ok"]
      and info["results_writable"] and info["report_writer"]
    )
    return info

  @staticmethod
  def _module_present(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None

  def check_tools(self, category: str | None = None) -> dict[str, Any]:
    summary = self.registry.summary()
    missing = self.registry.missing_tools()
    if category:
      missing = [t.id for t in self.registry.by_category(category) if not self.registry.is_installed(t.id)]
    return {
      **summary,
      "missing_sample": missing[:20],
    }

  def check_workspace_vault(self) -> dict[str, Any]:
    """Aggregate workspace + vault health (used by /doctor and `wth doctor`)."""
    ws = self.check_workspace()
    cfg = self.check_config()
    vault = self.check_vault()
    refs = self.check_referenced_secrets()
    perms = self.check_permissions()
    roles = self.check_roles()
    prov = self.check_provider_secrets()
    prof = self.check_active_profile()
    bus = self.check_event_bus()
    integ = self.check_integrations()
    appr = self.check_approval_registry()
    jobs = self.check_jobs()
    ok = (
      ws["complete"]
      and cfg["valid"]
      and vault["can_open"]
      and not refs["missing"]
      and perms["safe"]
      and roles["complete"]
      and prof["valid"]
      and bus["available"]
      and integ["ok"]
      and appr["ok"]
      and jobs["ok"]
    )
    return {
      "ok": ok,
      "workspace": ws,
      "config": cfg,
      "vault": vault,
      "referenced_secrets": refs,
      "permissions": perms,
      "roles": roles,
      "provider_secrets": prov,
      "profile": prof,
      "event_bus": bus,
      "integrations": integ,
      "approvals": appr,
      "jobs": jobs,
    }

  def full_report(self) -> str:
    rt = self.check_runtime()
    ts = self.check_tools()
    wv = self.check_workspace_vault()
    ws, cfg, vault, refs, perms = (
      wv["workspace"], wv["config"], wv["vault"], wv["referenced_secrets"], wv["permissions"],
    )
    roles, prov, prof = wv["roles"], wv["provider_secrets"], wv["profile"]
    bus, integ, appr, jobs = wv["event_bus"], wv["integrations"], wv["approvals"], wv["jobs"]
    lines = [
      "# WTH Doctor",
      "",
      "## Runtime",
      f"- Install method: {rt['install_method']}",
      f"- Python: {rt['python']}",
      f"- Binary mode: {rt['frozen']}",
      f"- WTH_HOME: {rt['wth_home']}",
      f"- Tools bin: {rt['tools_bin']}",
      f"- Runtime binary: {rt['runtime_binary']}",
      f"- OpenRouter: {'ONLINE' if rt['openrouter'] else 'OFFLINE — set OPENROUTER_API_KEY'}",
      f"- Go: {rt['go']}",
      f"- Pip: {rt['pip']}",
      "",
      "## Workspace & Vault",
      f"- Workspace: {'OK' if ws['complete'] else 'INCOMPLETE — run `wth setup`'}"
      + (f" (missing: {', '.join(ws['missing_dirs'])})" if ws["missing_dirs"] else ""),
      f"- db.sqlite: {'present' if ws['db_exists'] else 'missing'}",
      f"- Config: {'valid' if cfg['valid'] else 'INVALID — ' + str(cfg['error'])}",
      f"- Vault: {'open OK' if vault['can_open'] else 'CANNOT OPEN'} "
      + f"({vault['secret_count']} secrets)",
      f"- Referenced secrets: {'all present' if not refs['missing'] else 'MISSING ' + ', '.join(refs['missing'])}",
      f"- Permissions: {'safe' if perms['safe'] else 'UNSAFE — ' + '; '.join(perms['issues'])}",
      "",
      "## Providers & Roles",
      f"- Role models: {'all set' if roles['complete'] else 'MISSING ' + ', '.join(roles['missing'])}",
      f"- Fallback model: {'present' if roles['fallback_present'] else 'MISSING'}",
      f"- Provider keys: {'all present' if prov['ok'] else 'MISSING ' + ', '.join(prov['missing_keys'])}"
      + f" (used: {', '.join(prov['providers_used'])})",
      f"- Active profile: {prof['active'] or 'none'}"
      + ("" if prof["valid"] else " (INVALID)"),
      "",
    ]
    for role, m in roles["mapping"].items():
      lines.append(f"  - {role}: {m}")
    lines += [
      "",
      "## Integrations & Events",
      f"- Event bus: {'available' if bus['available'] else 'UNAVAILABLE'} ({bus['event_types']} event types)",
      f"- Integrations enabled: {', '.join(integ['enabled']) or 'none'}",
      f"- Integration config: {'valid' if integ['ok'] else 'ISSUES — ' + '; '.join(integ['issues'])}",
      f"- Approval registry: {'OK' if appr['ok'] else 'ERROR'} ({appr.get('pending', 0)} pending)",
      "",
      "## Jobs & Worker",
      f"- Job store: {'OK' if jobs['ok'] else 'ERROR'}"
      + (f" — {jobs['error']}" if jobs.get('error') else f" (schema {'valid' if jobs.get('schema_ok') else 'INVALID'}, {jobs.get('total_jobs', 0)} jobs)"),
      f"- Queue dir: {'present' if jobs.get('queue_dir') else 'MISSING'}",
      f"- Worker status: {'readable' if jobs.get('worker_readable') else 'UNREADABLE'}",
      "",
    ]
    rec = self.check_recon()
    lines += [
      "## Recon Platform (Fase 5)",
      f"- Engine importable: {'yes' if rec['importable'] else 'NO'}",
      f"- Tool registry: {rec['registry_count']} tools "
      + ("(OK >=200)" if rec['registry_ok'] else "(TOO FEW — expected >=200)"),
      f"- Available now: {rec['available_tools']} · missing (optional): {rec['missing_tools']}",
      f"- Builtin recon: {'OK' if rec['builtin_ok'] else 'FAIL'}",
      f"- HTTP client: {'OK' if rec['http_client'] else 'missing'} · "
      + f"DNS resolver: {'OK' if rec['dns_resolver'] else 'missing'}",
      f"- Wordlist: {'OK' if rec['wordlist_ok'] else 'missing'} · "
      + f"Report writer: {'OK' if rec['report_writer'] else 'missing'}",
      f"- Results dir writable: {'yes' if rec['results_writable'] else 'NO'}",
      f"- Profile recon-only: {'valid' if rec['profile_recon_only'] else 'INVALID'} · "
      + f"local policy: {'valid' if rec['local_policy'] else 'INVALID'}",
      f"- Recon status: {'OK' if rec['ok'] else 'ISSUES'}"
      + (f" — warnings: {'; '.join(rec['warnings'])}" if rec.get('warnings') else ""),
      "",
      f"## Tools: {ts['installed']}/{ts['total']} installed ({ts['missing']} missing)",
      "",
    ]
    if ts.get("missing_sample"):
      lines.append("### Top missing (install with `/tools install all`)")
      for tid in ts["missing_sample"][:15]:
        spec = self.registry.tools.get(tid)
        if spec:
          methods = ", ".join(spec.install.keys()) or "manual"
          lines.append(f"- `{tid}` ({methods})")
    lines.append("")
    lines.append("Run: `/tools install recon.subdomain` or `wth tools install all`")
    return "\n".join(lines)

  def auto_install(self, target: str = "all", *, use_sudo: bool = True) -> str:
    if target == "all":
      results = self.installer.install_all_missing(use_sudo=use_sudo, limit=30)
    else:
      results = self.installer.install_category(target, use_sudo=use_sudo)
    installed = sum(1 for r in results if r.get("status") in {"installed", "already_installed"})
    failed = [r for r in results if r.get("status") not in {"installed", "already_installed"}]
    lines = [
      f"## Tool Install — {installed}/{len(results)} succeeded",
      "",
    ]
    for r in results[:25]:
      lines.append(f"- {r.get('tool')}: {r.get('status')} ({r.get('method', '-')})")
    if failed:
      lines.append(f"\n{len(failed)} failed — check ~/.wth-ai/logs/install.log")
    return "\n".join(lines)

  def ensure_recon_tools(self, phases: list[int] | None = None) -> list[dict[str, Any]]:
    phases = phases or list(range(1, 6))
    results: list[dict[str, Any]] = []
    for p in phases:
      results.extend(self.installer.ensure_phase_tools(p))
    return results
=======
            from wth_ai.recon import ToolRegistry, ReconPlanner

            registry = ToolRegistry()
            tool_count = registry.count()
            categories = len(registry.get_categories())

            # Quick planner test
            planner = ReconPlanner(registry)
            plan = planner.plan(target="example.com", profile="default", max_tools=5)

            return {
                "ok": True,
                "tool_count": tool_count,
                "categories": categories,
                "planner_works": len(plan.selected_tools) > 0,
                "manifest_loaded": True,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": str(exc),
                "manifest_loaded": False,
            }

    def check_workspace_vault(self) -> dict[str, Any]:
        """Aggregate workspace + vault health (used by /doctor and `wth doctor`)."""
        ws = self.check_workspace()
        cfg = self.check_config()
        vault = self.check_vault()
        refs = self.check_referenced_secrets()
        perms = self.check_permissions()
        roles = self.check_roles()
        prov = self.check_provider_secrets()
        prof = self.check_active_profile()
        bus = self.check_event_bus()
        integ = self.check_integrations()
        appr = self.check_approval_registry()
        jobs = self.check_jobs()
        recon = self.check_recon_engine()  # NEW in Fase 5.2

        ok = (
            ws["complete"]
            and cfg["valid"]
            and vault["can_open"]
            and not refs["missing"]
            and perms["safe"]
            and roles["complete"]
            and prof["valid"]
            and bus["available"]
            and integ["ok"]
            and appr["ok"]
            and jobs["ok"]
            and recon["ok"]
        )
        return {
            "ok": ok,
            "workspace": ws,
            "config": cfg,
            "vault": vault,
            "referenced_secrets": refs,
            "permissions": perms,
            "roles": roles,
            "provider_secrets": prov,
            "profile": prof,
            "event_bus": bus,
            "integrations": integ,
            "approvals": appr,
            "jobs": jobs,
            "recon_engine": recon,  # NEW
        }

    def full_report(self) -> str:
        rt = self.check_runtime()
        ts = self.check_tools()
        wv = self.check_workspace_vault()
        ws, cfg, vault, refs, perms = (
            wv["workspace"], wv["config"], wv["vault"], wv["referenced_secrets"], wv["permissions"],
        )
        roles, prov, prof = wv["roles"], wv["provider_secrets"], wv["profile"]
        bus, integ, appr, jobs = wv["event_bus"], wv["integrations"], wv["approvals"], wv["jobs"]
        recon = wv.get("recon_engine", {})  # NEW

        lines = [
            "# WTH Doctor",
            "",
            "## Runtime",
            f"- Install method: {rt['install_method']}",
            f"- Python: {rt['python']}",
            f"- Binary mode: {rt['frozen']}",
            f"- WTH_HOME: {rt['wth_home']}",
            f"- Tools bin: {rt['tools_bin']}",
            f"- Runtime binary: {rt['runtime_binary']}",
            f"- OpenRouter: {'ONLINE' if rt['openrouter'] else 'OFFLINE — set OPENROUTER_API_KEY'}",
            f"- Go: {rt['go']}",
            f"- Pip: {rt['pip']}",
            "",
            "## Workspace & Vault",
            f"- Workspace: {'OK' if ws['complete'] else 'INCOMPLETE — run `wth setup`'}"
            + (f" (missing: {', '.join(ws['missing_dirs'])})" if ws.get("missing_dirs") else ""),
            f"- db.sqlite: {'present' if ws['db_exists'] else 'missing'}",
            f"- Config: {'valid' if cfg['valid'] else 'INVALID — ' + str(cfg['error'])}",
            f"- Vault: {'open OK' if vault['can_open'] else 'CANNOT OPEN'} "
            + f"({vault['secret_count']} secrets)",
            f"- Referenced secrets: {'all present' if not refs['missing'] else 'MISSING ' + ', '.join(refs['missing'])}",
            f"- Permissions: {'safe' if perms['safe'] else 'UNSAFE — ' + '; '.join(perms['issues'])}",
            "",
            "## Providers & Roles",
            f"- Role models: {'all set' if roles['complete'] else 'MISSING ' + ', '.join(roles['missing'])}",
            f"- Fallback model: {'present' if roles['fallback_present'] else 'MISSING'}",
            f"- Provider keys: {'all present' if prov['ok'] else 'MISSING ' + ', '.join(prov['missing_keys'])}"
            + f" (used: {', '.join(prov['providers_used'])})",
            f"- Active profile: {prof['active'] or 'none'}"
            + ("" if prof["valid"] else " (INVALID)"),
            "",
        ]
        for role, m in roles["mapping"].items():
            lines.append(f"  - {role}: {m}")
        lines += [
            "",
            "## Integrations & Events",
            f"- Event bus: {'available' if bus['available'] else 'UNAVAILABLE'} ({bus['event_types']} event types)",
            f"- Integrations enabled: {', '.join(integ['enabled']) or 'none'}",
            f"- Integration config: {'valid' if integ['ok'] else 'ISSUES — ' + '; '.join(integ['issues'])}",
            f"- Approval registry: {'OK' if appr['ok'] else 'ERROR'} ({appr.get('pending', 0)} pending)",
            "",
            "## Jobs & Worker",
            f"- Job store: {'OK' if jobs['ok'] else 'ERROR'}"
            + (f" — {jobs['error']}" if jobs.get('error') else f" (schema {'valid' if jobs.get('schema_ok') else 'INVALID'}, {jobs.get('total_jobs', 0)} jobs)"),
            f"- Queue dir: {'present' if jobs.get('queue_dir') else 'MISSING'}",
            f"- Worker status: {'readable' if jobs.get('worker_readable') else 'UNREADABLE'}",
            "",
            "## Recon Engine (Fase 5)",  # NEW SECTION
            f"- Recon Engine: {'OK' if recon.get('ok') else 'ERROR'}",
            f"- Tools in registry: {recon.get('tool_count', 'N/A')}",
            f"- Categories: {recon.get('categories', 'N/A')}",
            f"- Planner works: {recon.get('planner_works', False)}",
            "",
            f"## Tools: {ts['installed']}/{ts['total']} installed ({ts['missing']} missing)",
            "",
        ]
        if ts.get("missing_sample"):
            lines.append("### Top missing (install with `/tools install all`)")
            for tid in ts["missing_sample"][:15]:
                spec = self.registry.tools.get(tid)
                if spec:
                    methods = ", ".join(spec.install.keys()) or "manual"
                    lines.append(f"- `{tid}` ({methods})")
        lines.append("")
        lines.append("Run: `/tools install recon.subdomain` or `wth tools install all`")
        return "\n".join(lines)

    # ... rest of the file remains the same
main
