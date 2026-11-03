"""OpenRouter tool definitions for supervisor agent."""

from __future__ import annotations

from typing import Any

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "scope_set": {
        "type": "function",
        "function": {
            "name": "scope_set",
            "description": "Set assessment scope: target domain/URL, authorization, environment",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "authorized": {"type": "boolean"},
                    "environment": {"type": "string", "enum": ["production", "staging", "development", "local"]},
                    "in_scope": {"type": "array", "items": {"type": "string"}},
                    "rate_limit_rps": {"type": "number"},
                },
                "required": ["target"],
            },
        },
    },
    "scope_show": {
        "type": "function",
        "function": {
            "name": "scope_show",
            "description": "Show current scope configuration",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "research": {
        "type": "function",
        "function": {
            "name": "research",
            "description": (
                "Cari/pelajari informasi PUBLIK tentang teknologi atau kerentanan yang kamu temukan "
                "di target (CVE, dokumentasi vendor, perilaku framework, write-up publik). Pakai ini "
                "saat kamu belum tahu sesuatu tentang stack target. Hasilnya SELALU berstatus "
                "KANDIDAT: informasi dari internet tidak pernah menjadi bukti tentang target ini, jadi "
                "jangan pernah melaporkannya sebagai temuan tanpa verifikasi langsung."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "fetch", "browser", "notes"],
                        "description": "search=kata kunci; fetch=ambil 1 URL; browser=URL butuh JS; notes=catatan sesi ini",
                    },
                    "query": {"type": "string", "description": "kata kunci untuk action=search"},
                    "url": {"type": "string", "description": "URL absolut untuk action=fetch/browser"},
                    "limit": {"type": "integer", "description": "jumlah hasil maksimum (default 8)"},
                },
                "required": ["action"],
            },
        },
    },

    "computer_use": {
        "type": "function",
        "function": {
            "name": "computer_use",
            "description": (
                "Drive the operator's real desktop: observe (screenshot/windows/screen_size/"
                "cursor) or act (click/move/type/key/focus). Observation is read-only; input "
                "injection needs at least 'standard' permission and will not run in read-only/"
                "plan mode. Use it to look at a browser, an app, or to confirm something "
                "rendered -- not as a substitute for HTTP requests to a target."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "detect", "screenshot", "windows", "screen_size", "cursor",
                            "click", "move", "type", "key", "focus",
                        ],
                    },
                    "x": {"type": "integer", "description": "X koordinat (click/move)"},
                    "y": {"type": "integer", "description": "Y koordinat (click/move)"},
                    "button": {"type": "integer", "description": "tombol mouse, default 1"},
                    "text": {"type": "string", "description": "teks untuk action=type"},
                    "combo": {"type": "string", "description": "key combo untuk action=key, mis. ctrl+l"},
                    "window": {"type": "string", "description": "judul/id window untuk action=focus"},
                    "path": {"type": "string", "description": "tujuan file PNG untuk action=screenshot"},
                },
                "required": ["action"],
            },
        },
    },

    "permission_set": {
        "type": "function",
        "function": {
            "name": "permission_set",
            "description": (
                "Set the permission preset. 'plan'/'read-only' = no changes; 'ask' = safe "
                "default with confirmation; 'auto' = no prompts, bounded capability; 'yolo' = "
                "no prompts + max capability (the target must still be authorized)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        # Preset names are the CLI-facing vocabulary ("like any AI CLI").
                        # The raw PermissionMode values still resolve through
                        # `permission_presets.ALIASES`, so nothing that worked before breaks.
                        "enum": ["plan", "read-only", "ask", "auto", "yolo"],
                    },
                },
                "required": ["mode"],
            },
        },
    },
    "session_status": {
        "type": "function",
        "function": {
            "name": "session_status",
            "description": "Session phase, graph coverage, findings count, task queue",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "recon_list_tools": {
        "type": "function",
        "function": {
            "name": "recon_list_tools",
            "description": "List recon tools from registry (filter by category, availability)",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "available_only": {"type": "boolean", "default": False},
                },
            },
        },
    },
    "recon_run_tool": {
        "type": "function",
        "function": {
            "name": "recon_run_tool",
            "description": "Run one recon tool safely (builtin or external)",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": ["tool_name"],
            },
        },
    },
    "recon_finalize": {
        "type": "function",
        "function": {
            "name": "recon_finalize",
            "description": "Normalize recon results, score priority targets, write report",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "query_graph": {
        "type": "function",
        "function": {
            "name": "query_graph",
            "description": "Query asset graph summary and high-value parameters",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    },
    "http_probe": {
        "type": "function",
        "function": {
            "name": "http_probe",
            "description": "Send HTTP request to in-scope URL. Returns TX transcript id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]},
                    "url": {"type": "string"},
                    "purpose": {"type": "string"},
                    "body": {
                        "type": "string",
                        "description": (
                            "Badan permintaan (form/JSON/teks). Skema lama TIDAK "
                            "mencantumkan ini, jadi model tidak punya cara mengirim "
                            "body padahal handler-nya menerima -- penghalang nyata "
                            "untuk uji business logic."
                        ),
                    },
                    "compare_to": {
                        "type": "string",
                        "description": (
                            "TX id request sebelumnya. Hasilnya menyertakan DIFF "
                            "(status/ukuran/isi/sifat error) supaya perubahan satu "
                            "variabel bisa dibaca langsung -- ini jalur uji adaptif: "
                            "ubah satu hal, lihat apa yang berubah di server."
                        ),
                    },
                    "headers": {
                        "type": "object",
                        "description": (
                            "Optional request headers. For a target credential "
                            "(e.g. Authorization/session token), use a "
                            "'vault:<name>' reference as the value instead of "
                            "the plaintext secret -- it is resolved just-in-time "
                            "server-side and the real value is never sent back "
                            "to you or stored in any transcript."
                        ),
                    },
                },
                "required": ["url"],
            },
        },
    },
    "propose_payload": {
        "type": "function",
        "function": {
            "name": "propose_payload",
            "description": (
                "Ajukan payload BARU yang kamu susun sendiri (di luar katalog) untuk "
                "situasi spesifik di engagement ini. Payload divalidasi: wajib memuat "
                "proves + probe(url) dan cleanup bila destruktif. Yang berdampak massal "
                "(blast_radius 'mass') tetap DISIMPAN sebagai dokumentasi tetapi TIDAK "
                "pernah dijalankan mesin. Payload destruktif hanya jalan bila engagement "
                "membuka opt-in; tanpa itu ia tersimpan dan tidak dieksekusi."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vuln_class": {"type": "string"},
                    "title": {"type": "string"},
                    "proves": {"type": "string", "description": "Temuan apa yang dibuktikan."},
                    "probe": {
                        "type": "object",
                        "description": "Argumen http_probe: method, url, body, headers.",
                    },
                    "blast_radius": {"type": "string", "enum": ["self", "single-object", "mass"]},
                    "reversible": {"type": "boolean"},
                    "cleanup": {"type": "string", "description": "Wajib bila mengubah/menghapus data."},
                    "requires_destructive": {"type": "boolean"},
                    "run_now": {"type": "boolean", "description": "Jalankan bila batas mengizinkan."},
                },
                "required": ["vuln_class", "title", "proves", "probe"],
            },
        },
    },
    "oob_create": {
        "type": "function",
        "function": {
            "name": "oob_create",
            "description": (
                "Buat token callback OOB (out-of-band) milik engagement ini: URL yang "
                "HARUS dihubungi server target bila uji blind SSRF/XXE/RCE/blind XSS "
                "berhasil. Listener default hanya loopback (127.0.0.1); host/port "
                "tidak bisa diubah dari argumen tool. Setelah URL dikirim ke vektor "
                "uji, panggil oob_check untuk melihat hit — hit tercatat sebagai "
                "bukti, bukan klaim."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Penanda singkat, mis. 'ssrf-url-param'."},
                    "ttl_minutes": {"type": "integer", "description": "Masa hidup token dalam menit (default 60)."},
                },
                "required": [],
            },
        },
    },
    "oob_check": {
        "type": "function",
        "function": {
            "name": "oob_check",
            "description": (
                "Periksa apakah token callback OOB sudah dihubungi target. Bila ada "
                "hit, bukti disimpan sebagai transcript dan bisa dikutip [TX:...]. "
                "Tanpa hit, jangan klaim apa pun."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "token": {"type": "string", "description": "Token dari oob_create."},
                },
                "required": ["token"],
            },
        },
    },
    "run_shell": {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run shell command gated by permission mode",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "purpose": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
    "plan_hunt": {
        "type": "function",
        "function": {
            "name": "plan_hunt",
            "description": "Generate hunt plan and task queue from graph and goal",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                },
                "required": ["goal"],
            },
        },
    },
    "execute_task": {
        "type": "function",
        "function": {
            "name": "execute_task",
            "description": "Execute a task by id from the queue, or next queued task",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
            },
        },
    },
    "verify_finding": {
        "type": "function",
        "function": {
            "name": "verify_finding",
            "description": "Verify a finding candidate with evidence gate",
            "parameters": {
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string"},
                },
                "required": ["finding_id"],
            },
        },
    },
    "list_findings": {
        "type": "function",
        "function": {
            "name": "list_findings",
            "description": "List findings and hypotheses in current session",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "generate_report": {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": "Generate markdown security assessment report",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "store_evidence": {
        "type": "function",
        "function": {
            "name": "store_evidence",
            "description": "Link transcript to finding with evidence schema",
            "parameters": {
                "type": "object",
                "properties": {
                    "transcript_id": {"type": "string"},
                    "finding_id": {"type": "string"},
                    "vuln_class": {"type": "string"},
                },
                "required": ["transcript_id"],
            },
        },
    },
    "job_create_recon": {
        "type": "function",
        "function": {
            "name": "job_create_recon",
            "description": "Create background recon job via job manager",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "profile": {"type": "string", "default": "recon-only"},
                },
                "required": ["target"],
            },
        },
    },
    "job_poll": {
        "type": "function",
        "function": {
            "name": "job_poll",
            "description": "Poll status of a background job",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                },
                "required": ["job_id"],
            },
        },
    },
    "delegate_task": {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Spawn a focused subagent (injection, xss, auth, authz, ssrf, infra, ctf, reporter, recon)",
            "parameters": {
                "type": "object",
                "properties": {
                    "subagent": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["subagent", "prompt"],
            },
        },
    },
    "session_search": {
        "type": "function",
        "function": {
            "name": "session_search",
            "description": "Search FTS session memory across prior turns",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
}

ACTOR_TOOLS: dict[str, list[str]] = {
    "supervisor": list(TOOL_DEFINITIONS.keys()),
    "planner": ["query_graph", "http_probe", "store_evidence", "scope_show", "session_status", "oob_create", "oob_check"],
    "executor": ["query_graph", "http_probe", "run_shell", "store_evidence", "oob_create", "oob_check"],
    "recon": ["query_graph", "http_probe", "run_shell", "recon_list_tools", "recon_run_tool", "recon_finalize"],
}


def tools_for_actor(actor: str) -> list[dict[str, Any]]:
    names = ACTOR_TOOLS.get(actor, ACTOR_TOOLS["supervisor"])
    tools = [TOOL_DEFINITIONS[n] for n in names if n in TOOL_DEFINITIONS]
    try:
        from wth_ai.config import WTHConfig
        from wth_ai.mcp.manager import MCPClientManager
        cfg = WTHConfig.load()
        if cfg.mcp.enabled:
            mgr = MCPClientManager(cfg)
            mgr.connect_all()
            for server, mcp_tools in mgr.list_tools().items():
                for t in mcp_tools[:20]:
                    tname = t.get("name", "tool")
                    tools.append({
                        "type": "function",
                        "function": {
                            "name": f"mcp_{server}_{tname}",
                            "description": f"MCP {server}/{tname}",
                            "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
                        },
                    })
    except Exception:  # noqa: BLE001
        pass
    return tools
