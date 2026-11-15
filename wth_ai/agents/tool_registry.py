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
                        "enum": ["plan", "read-only", "ask", "auto", "yolo", "godmode"],
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
    "pipeline_surface": {
        "type": "function",
        "function": {
            "name": "pipeline_surface",
            "description": (
                "Inventory permukaan endpoint TANPA mengirim traffic: semua query param, "
                "key JSON body, dan form field. Pakai ini dulu supaya kamu tahu titik uji "
                "yang nyata sebelum memanggil pipeline_probe."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "URL absolut target"},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                    "headers": {"type": "object", "description": "Header request, mis. Content-Type"},
                    "body": {"type": "string", "description": "Body JSON/form mentah"},
                },
                "required": ["target"],
            },
        },
    },
    "pipeline_discover": {
        "type": "function",
        "function": {
            "name": "pipeline_discover",
            "description": (
                "Fetch SATU halaman seed (lewat http_probe teraudit), lalu ekstrak "
                "form, link same-origin, endpoint inline-JS, external JS same-origin "
                "(maks 5 file), import graph JS same-origin, plus OpenAPI JSON/YAML dan sitemap. "
                "Tidak melakukan probe/verifikasi; hasil tetap kandidat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "URL halaman absolut; default scope target"},
                    "max_surfaces": {"type": "integer", "default": 25, "minimum": 1, "maximum": 100},
                    "purpose": {"type": "string"},
                },
                "required": ["target"],
            },
        },
    },
    "pipeline_probe": {
        "type": "function",
        "function": {
            "name": "pipeline_probe",
            "description": (
                "Jalankan pipeline probe deterministik untuk satu kelas: baseline, probe "
                "per parameter, diff, dan verdict. Semua request lewat http_probe yang "
                "diaudit/scope-checked. Hasil verified hanya dari sinyal konkret; "
                "'needs_more_evidence' berarti belum ada bukti, bukan aman."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vuln_class": {
                        "type": "string",
                        "enum": ["sqli", "xss", "ssti", "cmdi", "path_traversal", "open_redirect", "idor", "auth", "race", "ssrf"],
                    },
                    "target": {"type": "string", "description": "URL absolut; default scope target"},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                    "headers": {"type": "object"},
                    "body": {"type": "string"},
                    "max_probes": {"type": "integer", "default": 12, "minimum": 1, "maximum": 30},
                    "purpose": {"type": "string"},
                },
                "required": ["vuln_class"],
            },
        },
    },
    "python_runtime": {
        "type": "function",
        "function": {
            "name": "python_runtime",
            "description": (
                "Jalankan SKRIP PYTHON yang kamu tulis sendiri (exploit runner, parser "
                "respons, brute tooling, PoC bespoke). Ini runtime nyata: kode berjalan "
                "di shell operator dengan permission/audit run_shell. Pakai untuk "
                "validasi PoC multi-step yang tidak bisa ekspresikan lewat http_probe."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Kode python utuh (requests otomatis tersedia jika ada di env)."},
                    "purpose": {"type": "string"},
                },
                "required": ["code"],
            },
        },
    },
    "self_improve": {
        "type": "function",
        "function": {
            "name": "self_improve",
            "description": (
                "Catat pelajaran dari langkah ini (what worked/failed, bypass that "
                "landed, gotcha stack target). Tersimpan persist antar sesi dan "
                "di-inject ke context; kamu MENINGKAT sendiri di run berikut. Panggil "
                "otomatis setiap kali menemukan sesuatu yang berharga — jangan tunggu diminta."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "mis. bypass, stack-bias, target:example.com"},
                    "lesson": {"type": "string"},
                    "evidence": {"type": "string", "description": "TX/akar bukti"},
                },
                "required": ["lesson"],
            },
        },
    },
    "memory_write": {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": "Simpan fakta kampanye persist (token ditemukan, endpoint menarik, gotcha automation).",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
        },
    },
    "memory_read": {
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": "Baca memori persist: notes kampanye + pelajaran self-improve.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "opsional; kosong = semua"},
                    "lesson_topic": {"type": "string", "description": "filter pelajaran"},
                    "limit": {"type": "integer"},
                },
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
    "malware_forge": {
        "type": "function",
        "function": {
            "name": "malware_forge",
            "description": (
                "Hasilkan malware/artefak nyata: shellcode runner, reflective PE loader, "
                "TCP implant, HTTP stager, dropper, atau beacon. Menulis source + README + "
                "metadata, optional compile, dan analisis statis otomatis. Hasil bukan sketsa."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["shellcode", "loader", "implant", "dropper", "stager", "beacon"],
                        "description": "shellcode=runner mentah; loader=muat PE dari memori; implant=shell TCP; stager=unduh stage2",
                    },
                    "language": {"type": "string", "enum": ["c", "powershell", "python", "bash"], "default": "c"},
                    "os": {"type": "string", "enum": ["windows", "linux", "macos"], "default": "windows"},
                    "arch": {"type": "string", "enum": ["x64", "x86", "arm64"], "default": "x64"},
                    "c2": {"type": "string", "description": "host:port untuk implant/beacon, atau URL stage2 untuk stager"},
                    "features": {
                        "type": "array", "items": {"type": "string"},
                        "description": "amsi,etw,antidebug,sleepmask,tcp",
                    },
                    "build": {"type": "boolean", "default": True, "description": "compile bila toolchain ada"},
                    "name": {"type": "string", "description": "nama artefak opsional"},
                    "payload": {"type": "string", "description": "payload msfvenom opsional, mis. linux/x64/exec (shellcode/dropper = payload asli)"},
                    "opts": {"type": "object", "additionalProperties": {"type": "string"}, "description": "opsi msfvenom, mis. {\"CMD\": \"/bin/echo hi\"}"},
                },
                "required": ["kind"],
            },
        },
    },
    "malware_encode": {
        "type": "function",
        "function": {
            "name": "malware_encode",
            "description": "Encode payload (file/hex) dengan XOR kunci opsional; keluaran C/Python/PowerShell/hex/base64 plus stub decoder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "payload": {"type": "string", "description": "path file atau string hex"},
                    "key": {"type": "integer", "default": 90},
                    "format": {"type": "string", "enum": ["c", "python", "powershell", "hex", "base64"], "default": "c"},
                },
                "required": ["payload"],
            },
        },
    },
    "malware_analyze": {
        "type": "function",
        "function": {
            "name": "malware_analyze",
            "description": "Analisis statis file: hash, entropi, PE sections/imports, YARA, indikator C2/anti-analysis.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
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
    "oob_dns": {
        "type": "function",
        "function": {
            "name": "oob_dns",
            "description": (
                "Callback DNS (out-of-band tanpa HTTP): untuk blind XXE/SSRF/RCE yang "
                "memicu resolusi nama. Menyalakan listener DNS (loopback) dan "
                "mengembalikan hostname callback <token>.oob.local yang HARUS di-resolve "
                "target bila uji berhasil; hasil resolve dicatat sebagai hit lewat "
                "oob_check. Host/port tidak bisa diubah dari argumen tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "integer", "description": "Port DNS (default 5353 unprivileged; 53 butuh hak root)."},
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
    "solidity_audit": {
        "type": "function",
        "function": {
            "name": "solidity_audit",
            "description": (
                "Jalankan toolchain audit Solidity lokal: Foundry build/test atau Slither "
                "pada direktori proyek. Command dibentuk deterministic dan dieksekusi lewat "
                "run_shell, jadi permission/approval/transcript tetap sama. Gunakan 'doctor' "
                "untuk cek forge/slither/solc tanpa menyentuh proyek."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["doctor", "build", "test", "slither"],
                        "description": "doctor=cek binary; build=forge build; test=forge test; slither=analisis statis",
                    },
                    "path": {
                        "type": "string",
                        "description": "Direktori root proyek Solidity (wajib untuk build/test/slither).",
                    },
                    "match_path": {
                        "type": "string",
                        "description": "Opsional untuk action=test: filter --match-path, mis. test/Pool.t.sol.",
                    },
                },
                "required": ["action"],
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
    "malware": ["malware_forge", "malware_analyze", "malware_encode", "run_shell", "http_probe", "store_evidence"],
    "planner": ["query_graph", "http_probe", "pipeline_surface", "pipeline_discover", "pipeline_probe", "store_evidence", "scope_show", "session_status", "oob_create", "oob_dns", "oob_check", "solidity_audit"],
    "executor": ["query_graph", "http_probe", "pipeline_surface", "pipeline_discover", "pipeline_probe", "run_shell", "store_evidence", "oob_create", "oob_dns", "oob_check", "solidity_audit"],
    "recon": ["query_graph", "http_probe", "pipeline_surface", "pipeline_discover", "run_shell", "recon_list_tools", "recon_run_tool", "recon_finalize"],
}


def tools_for_actor(actor: str) -> list[dict[str, Any]]:
    from wth_ai.engagement.mode import is_no_limits

    if is_no_limits():
        names = list(TOOL_DEFINITIONS.keys())
    else:
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
