"""Endpoint LLM OpenAI-compatible milik operator (`/connect`).

Kenapa modul ini ada
--------------------
Router hanya bisa membangun provider dari tipe yang sudah dikenalnya
(`PROVIDER_NAMES`). Operator yang punya gateway sendiri — LM Studio, vLLM,
LiteLLM, 9router di port lain, proxy kantor — tidak punya cara menyebutnya:
`/provider set` menolak tipe asing, dan `custom` hanya menyediakan satu slot
global yang tidak bisa diberi nama.

Di sini provider itu menjadi data: satu entry di `custom_providers`, berisi
base URL, model default, dan *referensi* vault — bukan API key itu sendiri.
Aturannya sama dengan secret lain di repo ini: user.yaml hanya memegang
`vault:<name>`, plaintext-nya tinggal di `vault.enc`.

Autentikasi tetap keyless-friendly: endpoint lokal yang tidak memakai key
sah-sah saja, karena itu kasus utama fitur ini.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

from wth_ai.config import PROVIDER_NAMES, ProviderRoleConfig, WTHConfig
from wth_ai.llm.providers.openai_compat import OpenAICompatProvider

#: Probe `/models` memakai batas yang sama dengan `/model list`: cukup untuk
#: gateway lokal, tidak bisa membekukan REPL kalau endpoint-nya menggantung.
PROBE_TIMEOUT = 8

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

#: Nama yang tidak boleh dipakai: tipe provider bawaan sudah menempati slot itu
#: di router, jadi nama yang sama akan menutupi perilaku yang sudah ada.
RESERVED_NAMES = frozenset(PROVIDER_NAMES)


class ConnectError(ValueError):
    """Input operator tidak bisa dijadikan provider."""


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    models: tuple[str, ...]
    detail: str = ""


@dataclass(frozen=True)
class ConnectResult:
    name: str
    base_url: str
    model: str | None
    probe_ok: bool
    probe_detail: str = ""
    models: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# normalisasi
# --------------------------------------------------------------------------- #
def normalize_base_url(raw: str) -> str:
    """URL yang selalu berakhiran `/v1` dan tidak berakhiran garis miring.

    Operator mengetik endpoint dalam bentuk apa pun yang ada di dokumentasi
    alatnya: `127.0.0.1:1234`, `http://host/v1/`, `https://gw/openai/v1`.
    Router memanggil `{base_url}/chat/completions`, jadi bentuk akhirnya harus
    tepat satu `/v1` di belakang. Skema yang hilang dianggap `http` — membuang
    input karena itu hanya memaksa operator mengetik ulang.
    """
    text = (raw or "").strip()
    if not text:
        raise ConnectError("base URL kosong")
    if "://" not in text:
        text = f"http://{text}"
    scheme, _, rest = text.partition("://")
    if scheme.lower() not in {"http", "https"}:
        raise ConnectError(f"skema '{scheme}' tidak didukung (pakai http atau https)")
    if not rest.strip("/"):
        raise ConnectError("base URL belum punya host")
    text = text.rstrip("/")
    if not text.endswith("/v1"):
        text = f"{text}/v1"
    return text


def derive_name(base_url: str, existing: set[str] | None = None) -> str:
    """Nama provider dari host: `127.0.0.1:1234` → `127-0-0-1-1234`.

    Port ikut masuk nama karena dua endpoint di host yang sama (LM Studio di
    1234 dan gateway lain di 4000) harus bisa hidup berdampingan.
    """
    host = base_url.split("://", 1)[-1].split("/", 1)[0]
    slug = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")[:24] or "provider"
    taken = set(existing or ()) | RESERVED_NAMES
    if slug not in taken:
        return slug
    for i in range(2, 100):
        candidate = f"{slug}-{i}"
        if candidate not in taken:
            return candidate
    raise ConnectError(f"tidak bisa membuat nama unik dari '{host}'")


def secret_name(provider_name: str) -> str:
    """Nama secret di vault untuk satu provider."""
    return f"provider_{provider_name}_api_key"


# --------------------------------------------------------------------------- #
# probe
# --------------------------------------------------------------------------- #
def probe_endpoint(base_url: str, api_key: str | None) -> ProbeResult:
    """GET `/models`. Kegagalan dilaporkan, bukan dilempar.

    Endpoint yang hidup tapi menolak `/models` tetap bisa dipakai untuk chat,
    jadi hasil ini hanya keterangan — bukan syarat pendaftaran.
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = requests.get(f"{base_url}/models", headers=headers, timeout=PROBE_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return ProbeResult(False, (), f"{type(exc).__name__}: {exc}")
    except ValueError as exc:
        return ProbeResult(False, (), f"respons bukan JSON: {exc}")

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return ProbeResult(False, (), "respons /models tidak berisi 'data'")
    models = tuple(str(m["id"]) for m in data if isinstance(m, dict) and m.get("id"))
    return ProbeResult(True, models, "")


# --------------------------------------------------------------------------- #
# register / hapus
# --------------------------------------------------------------------------- #
def connect(
    config: WTHConfig,
    *,
    base_url: str,
    api_key: str | None = None,
    name: str = "",
    model: str = "",
    probe: bool = True,
) -> ConnectResult:
    """Daftarkan endpoint sebagai provider bernama (belum mengubah role).

    Base URL dinormalisasi lebih dulu supaya yang tersimpan di config adalah
    bentuk yang benar-benar dipakai router.
    """
    normalized = normalize_base_url(base_url)
    provider_name = (name or derive_name(normalized, set(config.custom_providers))).strip().lower()
    if not _NAME_RE.match(provider_name):
        raise ConnectError(
            "nama provider hanya boleh huruf kecil, angka, '-' atau '_' (maks 32 karakter)"
        )
    if provider_name in RESERVED_NAMES:
        raise ConnectError(f"'{provider_name}' adalah provider bawaan; pilih nama lain")

    key = (api_key or "").strip() or None
    key_ref: str | None = None
    if key:
        from wth_ai.vault import SecretVault, vault_ref

        vname = secret_name(provider_name)
        SecretVault().set(vname, key)
        key_ref = vault_ref(vname)

    result = ProbeResult(False, (), "")
    if probe:
        result = probe_endpoint(normalized, key)

    chosen_model = (model or "").strip() or (result.models[0] if result.models else "")

    config.custom_providers[provider_name] = ProviderRoleConfig(
        type=provider_name,
        model=chosen_model,
        base_url=normalized,
        api_key_ref=key_ref,
    )
    return ConnectResult(
        name=provider_name,
        base_url=normalized,
        model=chosen_model or None,
        probe_ok=result.ok,
        probe_detail=result.detail,
        models=result.models,
    )


def disconnect(config: WTHConfig, name: str) -> bool:
    """Hapus provider dan secret-nya. False kalau memang tidak ada."""
    provider_name = (name or "").strip().lower()
    if provider_name not in config.custom_providers:
        return False
    del config.custom_providers[provider_name]

    try:
        from wth_ai.vault import SecretVault

        SecretVault().delete(secret_name(provider_name))
    except Exception:  # noqa: BLE001 - vault rusak tidak boleh menggagalkan hapus logis
        pass
    return True


def provider_for(config: WTHConfig, name: str) -> OpenAICompatProvider:
    """Bangun provider siap pakai dari entry `custom_providers`."""
    spec = config.custom_providers.get((name or "").strip().lower())
    if spec is None:
        raise KeyError(name)

    api_key = spec.api_key_ref
    if api_key and api_key.startswith("vault:"):
        from wth_ai.vault import SecretVault

        try:
            api_key = SecretVault().resolve(api_key)
        except Exception:  # noqa: BLE001 - vault tidak terbaca = tanpa key
            api_key = None

    return OpenAICompatProvider(
        provider_name=spec.type,
        api_key=api_key,
        base_url=spec.base_url,
        temperature=config.models.temperature,
        max_tokens=config.models.max_tokens,
        default_timeout=config.models.executor_timeout_seconds,
    )
