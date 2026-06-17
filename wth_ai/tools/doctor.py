# ... (previous content remains the same until check_jobs)

    def check_recon_engine(self) -> dict[str, Any]:
        """Check Fase 5 Recon Engine health."""
        try:
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
