"""`/connect` — tambah provider LLM dari base URL + API key.

Alurnya mengikuti kebiasaan chat-CLI modern: `/connect` tanpa argumen bertanya
interaktif (URL, key, nama), sedangkan bentuk satu baris bisa dipakai skrip dan
test. Keduanya memanggil fungsi yang sama, jadi tidak ada jalur kedua yang
diam-diam berbeda perilaku.

API key tidak pernah dicetak dan tidak pernah ditulis ke user.yaml: yang
tersimpan hanya `vault:<name>`.
"""

from __future__ import annotations

import getpass
from typing import Any, Callable

from wth_ai.cli.ui.picker import interactive_available
from wth_ai.config import ROLE_NAMES, ProviderRoleConfig
from wth_ai.llm import connect as conn

USAGE = (
    "## /connect — tambah provider LLM (OpenAI-compatible)\n"
    "\n"
    "  `/connect`                                  wizard interaktif\n"
    "  `/connect <base_url> [api_key]`             daftar langsung\n"
    "        opsi: `--name <nama>` `--model <id>` `--no-probe`\n"
    "  `/connect list`                             provider tambahan yang terdaftar\n"
    "  `/connect test <nama>`                      uji `/models` endpoint\n"
    "  `/connect use <nama> [model]`               pasang ke semua role\n"
    "  `/connect remove <nama>`                    hapus provider + secret-nya\n"
    "\n"
    "Base URL contoh: `http://127.0.0.1:1234/v1` — `/v1` ditambahkan otomatis\n"
    "kalau belum ada. API key boleh kosong untuk endpoint lokal tanpa auth."
)

_VALUE_FLAGS = {"--name", "--model"}


def handle_connect(orch: Any, args: list[str]) -> str:
    if not args:
        if interactive_available():
            return run_connect_wizard(orch)
        return USAGE

    sub = args[0].strip().lower()
    if sub in {"help", "-h", "--help", "?"}:
        return USAGE
    if sub in {"list", "ls"}:
        return _list(orch)
    if sub in {"test", "ping"}:
        return _test(orch, args[1] if len(args) > 1 else "")
    if sub in {"use", "set"}:
        return _use(orch, args[1:])
    if sub in {"remove", "rm", "delete"}:
        return _remove(orch, args[1] if len(args) > 1 else "")

    positional, flags = _parse_args(args)
    if not positional:
        return USAGE
    return _register(
        orch,
        base_url=positional[0],
        api_key=positional[1] if len(positional) > 1 else "",
        name=flags.get("name", ""),
        model=flags.get("model", ""),
        probe="no_probe" not in flags,
    )


# --------------------------------------------------------------------------- #
# wizard
# --------------------------------------------------------------------------- #
def run_connect_wizard(
    orch: Any,
    *,
    prompt: Callable[[str], str] | None = None,
    secret: Callable[[str], str] | None = None,
    picker: Callable[..., str | None] | None = None,
) -> str:
    """Tanya URL → key → nama → model, lalu daftarkan.

    Setiap langkah punya jalur batal: Enter kosong di URL berarti tidak ada yang
    diubah, dan key boleh kosong karena endpoint lokal biasanya tanpa auth.
    """
    ask = prompt or input
    ask_secret = secret or getpass.getpass

    try:
        base_url = ask("Base URL (contoh: http://127.0.0.1:1234/v1): ").strip()
    except (EOFError, KeyboardInterrupt):
        return "Dibatalkan."
    if not base_url:
        return "Dibatalkan — base URL kosong."

    try:
        api_key = ask_secret("API key (kosongkan kalau endpoint lokal): ").strip()
    except (EOFError, KeyboardInterrupt):
        return "Dibatalkan."

    probe = conn.probe_endpoint(_try_normalize(base_url) or base_url, api_key or None)

    default_name = ""
    try:
        default_name = conn.derive_name(_try_normalize(base_url) or base_url, set(orch.config.custom_providers))
    except Exception:  # noqa: BLE001 - nama hanya saran, bukan syarat
        default_name = ""

    try:
        name = ask(f"Nama provider [{default_name}]: ").strip() or default_name
    except (EOFError, KeyboardInterrupt):
        return "Dibatalkan."

    model = ""
    if probe.ok and probe.models:
        choose = picker
        if choose is None:
            from wth_ai.cli.ui.picker import pick_from_list

            choose = pick_from_list
        model = choose(f"Pilih model default · {name or default_name}", list(probe.models)) or ""

    return _register(
        orch,
        base_url=base_url,
        api_key=api_key,
        name=name,
        model=model,
        probe=probe,
    )


# --------------------------------------------------------------------------- #
# subcommand
# --------------------------------------------------------------------------- #
def _register(
    orch: Any,
    *,
    base_url: str,
    api_key: str,
    name: str,
    model: str,
    probe: bool | conn.ProbeResult = True,
) -> str:
    try:
        if isinstance(probe, conn.ProbeResult):
            result = conn.connect(
                orch.config, base_url=base_url, api_key=api_key, name=name, model=model, probe=False
            )
            # Probe sudah dilakukan wizard; hasilnya dipakai apa adanya supaya
            # tidak menembak endpoint yang sama dua kali.
            result = conn.ConnectResult(
                name=result.name,
                base_url=result.base_url,
                model=model or result.model,
                probe_ok=probe.ok,
                probe_detail=probe.detail,
                models=probe.models,
            )
            if result.model:
                orch.config.custom_providers[result.name].model = result.model
        else:
            result = conn.connect(
                orch.config, base_url=base_url, api_key=api_key, name=name, model=model, probe=probe
            )
    except (conn.ConnectError, ValueError) as exc:
        return f"Gagal connect: {exc}"

    orch.client.forget(result.name)

    lines = [f"✓ provider `{result.name}` terdaftar — {result.base_url}"]
    lines.append(f"  secret: {'tersimpan di vault' if api_key else 'tanpa key (endpoint lokal)'}")
    if result.model:
        lines.append(f"  model default: `{result.model}`")

    if result.probe_ok:
        count = len(result.models)
        lines.append(f"  ✓ /models merespons — {count} model terlihat" if count else "  ✓ endpoint merespons")
    else:
        lines.append(f"  ⚠ endpoint belum merespons — {result.probe_detail or 'tanpa keterangan'}")
        lines.append("    Provider tetap dicatat; perbaiki URL/key lalu `/connect test <nama>`.")

    if result.model:
        lines.append(_set_all_roles(orch, result.name, result.model))
    else:
        lines.append("  Model belum dipilih — jalankan `/model list <nama>` lalu `/model set planner <id>`.")

    orch.config.save_user_config()
    lines.append("  Tersimpan di ~/.wth-ai/config/user.yaml (key hanya sebagai referensi vault).")
    return "\n".join(lines)


def _list(orch: Any) -> str:
    providers = orch.config.custom_providers
    if not providers:
        return "Belum ada provider tambahan. Jalankan `/connect` atau `/connect <base_url> <api_key>`."
    lines = ["## Provider tambahan", ""]
    for name, spec in sorted(providers.items()):
        model = spec.model or "(belum diset)"
        key = "vault" if spec.api_key_ref else "tanpa key"
        lines.append(f"- **{name}** — {spec.base_url} · model `{model}` · {key}")
    lines.append("")
    lines.append("Pakai: `/connect use <nama> [model]` · hapus: `/connect remove <nama>`")
    return "\n".join(lines)


def _test(orch: Any, name: str) -> str:
    key = (name or "").strip().lower()
    spec = orch.config.custom_providers.get(key)
    if spec is None:
        return f"Provider `{name}` tidak terdaftar. Lihat `/connect list`."

    from wth_ai.vault import SecretVault

    api_key = SecretVault().resolve(spec.api_key_ref) if spec.api_key_ref else None
    result = conn.probe_endpoint(spec.base_url, api_key)
    if not result.ok:
        return f"✗ `{key}` tidak merespons — {result.detail}\n  Endpoint: {spec.base_url}"
    shown = ", ".join(result.models[:8]) or "(tidak ada model)"
    more = f" … +{len(result.models) - 8}" if len(result.models) > 8 else ""
    return f"✓ `{key}` merespons — {len(result.models)} model: {shown}{more}"


def _use(orch: Any, args: list[str]) -> str:
    if not args:
        return "Usage: `/connect use <nama> [model]`"
    name = args[0].strip().lower()
    spec = orch.config.custom_providers.get(name)
    if spec is None:
        return f"Provider `{name}` tidak terdaftar. Lihat `/connect list`."

    model = " ".join(args[1:]).strip() or spec.model
    if not model:
        return (
            f"Provider `{name}` belum punya model default.\n"
            f"Sebutkan: `/connect use {name} <model-id>` atau `/model list {name}`."
        )

    spec.model = model
    message = _set_all_roles(orch, name, model)
    orch.config.save_user_config()
    return message


def _remove(orch: Any, name: str) -> str:
    key = (name or "").strip().lower()
    if not key:
        return "Usage: `/connect remove <nama>`"
    if not conn.disconnect(orch.config, key):
        return f"Provider `{key}` tidak terdaftar."

    orch.client.forget(key)
    moved = _detach_roles(orch, key)
    orch.config.save_user_config()

    lines = [f"✓ provider `{key}` dihapus (entry + secret)."]
    if moved:
        lines.append(f"  Role yang memakainya dipindah ke provider utama: {', '.join(moved)}.")
    lines.append("  Pilihan lain: `/connect list`.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _set_all_roles(orch: Any, provider_name: str, model: str) -> str:
    for role in ROLE_NAMES:
        old = orch.config.providers.get(role)
        orch.config.providers[role] = ProviderRoleConfig(
            type=provider_name,
            model=model,
            backup_model=old.backup_model if old else None,
        )
    for role in ROLE_NAMES:
        try:
            orch.client.set_role(role, provider_name, model)
        except Exception:  # noqa: BLE001 - klien stub di test boleh tidak punya set_role
            pass
    return f"✓ semua role ({len(ROLE_NAMES)}) → `{provider_name}` / `{model}`"


def _detach_roles(orch: Any, provider_name: str) -> list[str]:
    fallback = orch.config.provider_prefs.primary or "openrouter"
    moved: list[str] = []
    for role in ROLE_NAMES:
        spec = orch.config.providers.get(role)
        if spec is not None and spec.type == provider_name:
            spec.type = fallback
            moved.append(role)
    return moved


def _try_normalize(raw: str) -> str:
    try:
        return conn.normalize_base_url(raw)
    except ValueError:
        return ""


def _parse_args(args: list[str]) -> tuple[list[str], dict[str, str]]:
    """Pisahkan positional (url, key) dari flag `--name`/`--model`/`--no-probe`."""
    positional: list[str] = []
    flags: dict[str, str] = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--no-probe":
            flags["no_probe"] = ""
            i += 1
            continue
        matched = next((f for f in _VALUE_FLAGS if arg == f or arg.startswith(f"{f}=")), None)
        if matched:
            if "=" in arg:
                flags[matched.lstrip("-")] = arg.split("=", 1)[1]
                i += 1
            else:
                if i + 1 >= len(args):
                    flags[matched.lstrip("-")] = ""
                    i += 1
                else:
                    flags[matched.lstrip("-")] = args[i + 1]
                    i += 2
            continue
        positional.append(arg)
        i += 1
    return positional, flags