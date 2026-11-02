"""WTH-AI configuration loader."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

WTH_HOME = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))
USER_CONFIG_PATH = WTH_HOME / "config" / "user.yaml"
USER_ENV_PATH = WTH_HOME / ".env"

# Canonical agent role names (Fase 2).
ROLE_NAMES: tuple[str, ...] = (
    "planner",
    "recon",
    "executor",
    "code",
    "verifier",
    "reporter",
    "fallback",
)

# Provider types the router can construct.
PROVIDER_NAMES: tuple[str, ...] = (
    "9router",
    "openrouter",
    "openai",
    "anthropic",
    "google",
    "groq",
    "ollama",
    "custom",
)

load_dotenv(USER_ENV_PATH)
load_dotenv()


@dataclass
class ModelConfig:
    planner: str = "nvidia/nemotron-3-super-120b-a12b:free"
    planner_backup: str = "qwen/qwen3-coder:free"
    executor: str = "qwen/qwen3-coder:free"
    executor_backup: str = "qwen/qwen3-coder-480b-a35b:free"
    verifier: str = "nvidia/nemotron-3-super-120b-a12b:free"
    report: str = "qwen/qwen3-coder:free"
    # Fase 2 role defaults (used as fallback when no per-role provider configured).
    recon: str = "nvidia/nemotron-3-super-120b-a12b:free"
    code: str = "qwen/qwen3-coder:free"
    reporter: str = "qwen/qwen3-coder:free"
    fallback: str = "qwen/qwen3-coder:free"
    max_tokens: int = 8192
    temperature: float = 0.3
    planner_timeout_seconds: int = 90
    executor_timeout_seconds: int = 120
    streaming: bool = True


@dataclass
class ProviderRoleConfig:
    type: str = "openrouter"
    model: str = ""
    backup_model: str | None = None
    base_url: str | None = None


@dataclass
class SecretsConfig:
    router9_api_key: str | None = None
    openrouter_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    groq_api_key: str | None = None
    custom_api_key: str | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"
    router9_base_url: str = "http://127.0.0.1:20128/v1"
    openai_base_url: str | None = None
    anthropic_base_url: str | None = None
    google_base_url: str | None = None
    custom_base_url: str | None = None
    # Research (search) backend. Optional: without a key the researcher falls
    # back to scraping search-engines' HTML, which on this network returns a
    # low-fidelity page for scriptless clients (verified: a Next.js CVE query
    # came back dominated by the fashion retailer "Next"). A keyed API returns
    # clean JSON, so it is tried FIRST whenever it is configured.
    search_provider: str = ""           # brave | (kosong = HTML fallback saja)
    search_api_key: str | None = None


@dataclass
class ProviderPrefsConfig:
    primary: str = "9router"
    failover_order: list[str] = field(
        default_factory=lambda: ["9router", "openrouter", "anthropic", "groq", "ollama"]
    )
    failover_enabled: bool = True


@dataclass
class SafetyConfig:
    unrestricted: bool = False  # dimensi 2 (capability ceiling) -- lihat engagement/mode.py
    default_permission: str = "full_lab"
    require_authorized: bool = True  # dimensi 1 (scope authorization) -- WU-003, default AMAN


@dataclass
class BotConfig:
    discord_token: str | None = None
    discord_guild_ids: list[int] = field(default_factory=list)
    discord_channel_ids: list[int] = field(default_factory=list)
    telegram_token: str | None = None
    telegram_chat_ids: list[int] = field(default_factory=list)


# Default notification rules for integrations (Fase 3).
DEFAULT_NOTIFY_RULES: dict[str, bool] = {
    "job_started": True,
    "approval_needed": True,
    "high_priority_finding": True,
    "report_ready": True,
    "job_failed": True,
    "job_completed": False,
    "provider_error": True,
    "integration_error": True,
}

DEFAULT_DISCORD_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "owner": ["*"],
    "admin": ["status", "jobs", "approve", "deny", "stop", "report"],
    "analyst": ["status", "jobs", "report"],
    "viewer": ["status", "report"],
}


@dataclass
class TelegramIntegration:
    enabled: bool = False
    bot_token_ref: str | None = None  # e.g. "vault:telegram_bot_token"
    allowed_chat_ids: list[str] = field(default_factory=list)
    owner_user_ids: list[str] = field(default_factory=list)  # WU-009 (NET-01): role real, bukan hardcode
    notify: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_NOTIFY_RULES))


@dataclass
class DiscordIntegration:
    enabled: bool = False
    bot_token_ref: str | None = None  # e.g. "vault:discord_bot_token"
    guild_id: str | None = None
    allowed_channel_ids: list[str] = field(default_factory=list)
    role_permissions: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_DISCORD_ROLE_PERMISSIONS.items()}
    )
    notify: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_NOTIFY_RULES))


@dataclass
class WebhookIntegration:
    enabled: bool = False
    url: str | None = None
    notify: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_NOTIFY_RULES))


@dataclass
class IntegrationsConfig:
    telegram: TelegramIntegration = field(default_factory=TelegramIntegration)
    discord: DiscordIntegration = field(default_factory=DiscordIntegration)
    webhook: WebhookIntegration = field(default_factory=WebhookIntegration)


@dataclass
class ReconConfig:
    adaptive_graph: bool = True
    rules_file: str = "wth_ai/recon/recon_rules.yaml"


@dataclass
class IntelConfig:
    enabled: bool = True
    nuclei_templates_check: bool = True
    wayback_delta: bool = True


@dataclass
class MCPConfig:
    enabled: bool = False
    servers: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=lambda: {
        "allowed_tools": ["*"],
        "max_calls_per_minute": 0,
        "audit_log": True,
    })


@dataclass
class NineRouterConfig:
    base_url: str = "http://127.0.0.1:20128/v1"
    api_key_env: str = "ROUTER9_API_KEY"


@dataclass
class OpenRouterConfig:
    api_key_env: str = "OPENROUTER_API_KEY"
    base_url: str = "https://openrouter.ai/api/v1"
    site_url: str = "https://wth-ai.local"
    app_name: str = "WTH-AI"


@dataclass
class SessionConfig:
    state_dir: str = "data/sessions"
    auto_save: bool = True
    resume_on_start: bool = True
    max_conversation_turns: int = 100


@dataclass
class ToolConfig:
    timeout_seconds: int = 120
    max_output_bytes: int = 512_000
    default_rate_limit_rps: float = 2.0


@dataclass
class SkillsConfig:
    openclaw_path: Path = field(default_factory=lambda: Path.home() / ".openclaw/workspace/skills")
    codex_path: Path = field(default_factory=lambda: Path.home() / ".codex/skills")
    local_path: Path = field(default_factory=lambda: Path("skills"))
    auto_discover: bool = True
    fusion_max_chars: int = 8000


@dataclass
class BrowserConfig:
    headless: bool = True
    cloudflare_wait: float = 8.0
    page_load_wait: float = 5.0


@dataclass
class AgentConfig:
    max_iters: int = 50
    recon_profile: str = "recon-only"
    stream_tools: bool = True
    auto_approve_unrestricted: bool = False  # dimensi 3 (human approval) -- WU-003, default AMAN
    mode: str = "hybrid"  # hybrid | chat | security


@dataclass
class UIConfig:
    mode: str = "plain"  # plain | tui
    # Default False = auto: penuh di terminal, compact saat dipipe (lihat
    # cli/ui/renderer.compact_enabled). Set True untuk memaksa ringkas.
    compact: bool = False
    verbose: bool = True
    activity_mode: str = "live"  # live | stream | quiet
    animations: bool = True
    show_tool_trace: bool = True
    show_progress_bar: bool = True
    stream_tokens: bool = True
    banner: bool = True
    theme: str = "dark"
    max_trace_lines: int = 20


@dataclass
class WTHConfig:
    models: ModelConfig = field(default_factory=ModelConfig)
    providers: dict[str, ProviderRoleConfig] = field(default_factory=dict)
    secrets: SecretsConfig = field(default_factory=SecretsConfig)
    bots: BotConfig = field(default_factory=BotConfig)
    integrations: IntegrationsConfig = field(default_factory=IntegrationsConfig)
    openrouter: OpenRouterConfig = field(default_factory=OpenRouterConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    tools: ToolConfig = field(default_factory=ToolConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    recon: ReconConfig = field(default_factory=ReconConfig)
    intel: IntelConfig = field(default_factory=IntelConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    nine_router: NineRouterConfig = field(default_factory=NineRouterConfig)
    provider_prefs: ProviderPrefsConfig = field(default_factory=ProviderPrefsConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    prompts_dir: str = "prompts"
    results_dir: str = "wth-results"
    require_openrouter: bool = False
    realtime_only: bool = True
    require_llm: bool = False
    setup_complete: bool = False
    default_permission: str = "read_only"
    user_config_path: Path = field(default_factory=lambda: USER_CONFIG_PATH)

    @classmethod
    def load(cls, path: str | Path = "config/wth_config.yaml") -> WTHConfig:
        path = Path(path)
        raw: dict[str, Any] = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        user_raw: dict[str, Any] = {}
        if USER_CONFIG_PATH.exists():
            user_raw = yaml.safe_load(USER_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            raw = _deep_merge(raw, user_raw)

        models_raw = raw.get("models", {})
        or_raw = raw.get("openrouter", {})
        session_raw = raw.get("session", {})
        tools_raw = raw.get("tools", {})
        skills_raw = raw.get("skills", {})
        browser_raw = raw.get("browser", {})
        recon_raw = raw.get("recon", {})
        intel_raw = raw.get("intel", {})
        mcp_raw = raw.get("mcp", {})
        mcp_servers = mcp_raw.get("servers", {})
        if isinstance(mcp_servers, list):
            mcp_servers = {name: {} for name in mcp_servers}
        mcp_cfg = {k: v for k, v in mcp_raw.items() if k in MCPConfig.__dataclass_fields__ and k != "servers"}
        mcp_cfg["servers"] = mcp_servers if isinstance(mcp_servers, dict) else {}
        safety_raw = raw.get("safety", {})
        agent_raw = raw.get("agent", {})
        ui_raw = raw.get("ui", {})
        prefs_raw = raw.get("provider_prefs", {})
        nr_raw = raw.get("nine_router", {})
        secrets_raw = raw.get("secrets", {})
        bots_raw = raw.get("bots", {})
        providers_raw = raw.get("providers", {})

        skills_cfg = SkillsConfig(**{k: v for k, v in skills_raw.items() if k in SkillsConfig.__dataclass_fields__})
        if "openclaw_path" in skills_raw:
            skills_cfg.openclaw_path = Path(str(skills_raw["openclaw_path"])).expanduser()
        if "codex_path" in skills_raw:
            skills_cfg.codex_path = Path(str(skills_raw["codex_path"])).expanduser()
        if "local_path" in skills_raw:
            skills_cfg.local_path = Path(str(skills_raw["local_path"])).expanduser()

        providers: dict[str, ProviderRoleConfig] = {}
        for role, pdata in providers_raw.items():
            if isinstance(pdata, dict):
                providers[role] = ProviderRoleConfig(
                    type=pdata.get("type", "openrouter"),
                    model=pdata.get("model", ""),
                    backup_model=pdata.get("backup_model"),
                    base_url=pdata.get("base_url"),
                )

        resolve = _make_secret_resolver()

        def _sec(field: str, env_key: str) -> str | None:
            return resolve(secrets_raw.get(field), env_key)

        secrets = SecretsConfig(
            router9_api_key=_sec("router9_api_key", "ROUTER9_API_KEY") or os.environ.get("9ROUTER_API_KEY"),
            openrouter_api_key=_sec("openrouter_api_key", "OPENROUTER_API_KEY"),
            openai_api_key=_sec("openai_api_key", "OPENAI_API_KEY"),
            anthropic_api_key=_sec("anthropic_api_key", "ANTHROPIC_API_KEY"),
            google_api_key=_sec("google_api_key", "GOOGLE_API_KEY"),
            groq_api_key=_sec("groq_api_key", "GROQ_API_KEY"),
            custom_api_key=_sec("custom_api_key", "CUSTOM_API_KEY"),
            ollama_base_url=secrets_raw.get("ollama_base_url", "http://127.0.0.1:11434"),
            router9_base_url=secrets_raw.get("router9_base_url", nr_raw.get("base_url", "http://127.0.0.1:20128/v1")),
            openai_base_url=secrets_raw.get("openai_base_url"),
            anthropic_base_url=secrets_raw.get("anthropic_base_url"),
            google_base_url=secrets_raw.get("google_base_url"),
            custom_base_url=secrets_raw.get("custom_base_url"),
            search_provider=secrets_raw.get("search_provider", ""),
            search_api_key=_sec("search_api_key", "SEARCH_API_KEY"),
        )

        bots = BotConfig(
            discord_token=resolve(bots_raw.get("discord_token"), "DISCORD_BOT_TOKEN"),
            discord_guild_ids=[int(x) for x in bots_raw.get("discord_guild_ids", [])],
            discord_channel_ids=[int(x) for x in bots_raw.get("discord_channel_ids", [])],
            telegram_token=resolve(bots_raw.get("telegram_token"), "TELEGRAM_BOT_TOKEN"),
            telegram_chat_ids=[int(x) for x in bots_raw.get("telegram_chat_ids", [])],
        )

        _inject_secret_env(secrets, bots)

        integrations = _parse_integrations(raw.get("integrations", {}))

        cfg = cls(
            models=ModelConfig(**{k: v for k, v in models_raw.items() if k in ModelConfig.__dataclass_fields__}),
            providers=providers,
            secrets=secrets,
            bots=bots,
            integrations=integrations,
            openrouter=OpenRouterConfig(**{k: v for k, v in or_raw.items() if k in OpenRouterConfig.__dataclass_fields__}),
            session=SessionConfig(**{k: v for k, v in session_raw.items() if k in SessionConfig.__dataclass_fields__}),
            tools=ToolConfig(**{k: v for k, v in tools_raw.items() if k in ToolConfig.__dataclass_fields__}),
            skills=skills_cfg,
            browser=BrowserConfig(**{k: v for k, v in browser_raw.items() if k in BrowserConfig.__dataclass_fields__}),
            recon=ReconConfig(**{k: v for k, v in recon_raw.items() if k in ReconConfig.__dataclass_fields__}),
            intel=IntelConfig(**{k: v for k, v in intel_raw.items() if k in IntelConfig.__dataclass_fields__}),
            mcp=MCPConfig(**mcp_cfg),
            nine_router=NineRouterConfig(**{k: v for k, v in nr_raw.items() if k in NineRouterConfig.__dataclass_fields__}),
            provider_prefs=ProviderPrefsConfig(**{k: v for k, v in prefs_raw.items() if k in ProviderPrefsConfig.__dataclass_fields__}),
            safety=SafetyConfig(**{k: v for k, v in safety_raw.items() if k in SafetyConfig.__dataclass_fields__}),
            agent=AgentConfig(**{k: v for k, v in agent_raw.items() if k in AgentConfig.__dataclass_fields__}),
            ui=UIConfig(**{k: v for k, v in ui_raw.items() if k in UIConfig.__dataclass_fields__}),
            prompts_dir=raw.get("prompts_dir", "prompts"),
            results_dir=raw.get("results_dir", "wth-results"),
            require_openrouter=raw.get("require_openrouter", False),
            realtime_only=raw.get("realtime_only", True),
            require_llm=raw.get("require_llm", user_raw.get("setup_complete", False)),
            setup_complete=raw.get("setup_complete", False),
            default_permission=raw.get("default_permission", safety_raw.get("default_permission", "full_lab")),
        )
        return cfg

    def api_key(self) -> str | None:
        return self.secrets.openrouter_api_key or os.environ.get(self.openrouter.api_key_env)

    def save_user_config(self) -> Path:
        """Persist config to ~/.wth-ai/config/user.yaml.

        Secrets are NEVER written in plaintext: only ``vault:<name>`` references
        are stored, and only for secrets that are present in the vault.
        """
        from wth_ai.vault import SecretVault, vault_ref

        USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        vault = SecretVault()
        present = set(vault.list_keys()) if vault.can_open() else set()

        secrets_out: dict[str, Any] = {
            "ollama_base_url": self.secrets.ollama_base_url,
            "router9_base_url": self.secrets.router9_base_url,
        }
        # Any endpoint override the operator set must survive a save/reload.
        # Only custom_base_url used to be written, so a base URL configured for
        # openai/anthropic/google (a proxy, a gateway, a self-hosted mirror) was
        # silently dropped the next time the config was saved.
        if self.secrets.search_provider:
            secrets_out["search_provider"] = self.secrets.search_provider
        for field in ("custom_base_url", "openai_base_url", "anthropic_base_url", "google_base_url"):
            value = getattr(self.secrets, field, None)
            if value:
                secrets_out[field] = value
        for field, vname in (
            ("router9_api_key", "router9_api_key"),
            ("openrouter_api_key", "openrouter_api_key"),
            ("openai_api_key", "openai_api_key"),
            ("anthropic_api_key", "anthropic_api_key"),
            ("google_api_key", "google_api_key"),
            ("groq_api_key", "groq_api_key"),
            ("custom_api_key", "custom_api_key"),
            ("search_api_key", "search_api_key"),
        ):
            if vname in present:
                secrets_out[field] = vault_ref(vname)

        bots_out: dict[str, Any] = {
            "discord_guild_ids": self.bots.discord_guild_ids,
            "discord_channel_ids": self.bots.discord_channel_ids,
            "telegram_chat_ids": self.bots.telegram_chat_ids,
        }
        if "discord_bot_token" in present:
            bots_out["discord_token"] = vault_ref("discord_bot_token")
        if "telegram_bot_token" in present:
            bots_out["telegram_token"] = vault_ref("telegram_bot_token")

        ig = self.integrations
        integrations_out = {
            "telegram": {
                "enabled": ig.telegram.enabled,
                "bot_token_ref": ig.telegram.bot_token_ref,
                "allowed_chat_ids": ig.telegram.allowed_chat_ids,
                "owner_user_ids": ig.telegram.owner_user_ids,
                "notify": ig.telegram.notify,
            },
            "discord": {
                "enabled": ig.discord.enabled,
                "bot_token_ref": ig.discord.bot_token_ref,
                "guild_id": ig.discord.guild_id,
                "allowed_channel_ids": ig.discord.allowed_channel_ids,
                "role_permissions": ig.discord.role_permissions,
                "notify": ig.discord.notify,
            },
            "webhook": {
                "enabled": ig.webhook.enabled,
                "url": ig.webhook.url,
                "notify": ig.webhook.notify,
            },
        }

        data: dict[str, Any] = {
            "setup_complete": self.setup_complete,
            "realtime_only": self.realtime_only,
            "require_llm": self.require_llm,
            "default_permission": self.default_permission,
            "safety": {
                "unrestricted": self.safety.unrestricted,
                "default_permission": self.safety.default_permission,
                "require_authorized": self.safety.require_authorized,
            },
            "provider_prefs": {
                "primary": self.provider_prefs.primary,
                "failover_order": self.provider_prefs.failover_order,
                "failover_enabled": self.provider_prefs.failover_enabled,
            },
            "providers": {
                role: {
                    "type": p.type,
                    "model": p.model,
                    "backup_model": p.backup_model,
                    **({"base_url": p.base_url} if p.base_url else {}),
                }
                for role, p in self.providers.items()
            },
            "secrets": secrets_out,
            "bots": bots_out,
            "integrations": integrations_out,
        }
        USER_CONFIG_PATH.write_text(yaml.safe_dump(data, default_flow_style=False), encoding="utf-8")
        from wth_ai.workspace import secure_file
        secure_file(USER_CONFIG_PATH)
        return USER_CONFIG_PATH

    def write_env_file(self) -> Path:
        """Write secrets to ~/.wth-ai/.env for subprocess compatibility."""
        lines: list[str] = []
        if self.secrets.openrouter_api_key:
            lines.append(f"OPENROUTER_API_KEY={self.secrets.openrouter_api_key}")
        if self.secrets.openai_api_key:
            lines.append(f"OPENAI_API_KEY={self.secrets.openai_api_key}")
        if self.secrets.anthropic_api_key:
            lines.append(f"ANTHROPIC_API_KEY={self.secrets.anthropic_api_key}")
        if self.secrets.google_api_key:
            lines.append(f"GOOGLE_API_KEY={self.secrets.google_api_key}")
        if self.bots.discord_token:
            lines.append(f"DISCORD_BOT_TOKEN={self.bots.discord_token}")
        if self.bots.telegram_token:
            lines.append(f"TELEGRAM_BOT_TOKEN={self.bots.telegram_token}")
        USER_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        USER_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # WU-210 (DATA-04): this file holds PLAINTEXT provider/bot secrets --
        # chmod 600 immediately after writing (same treatment as USER_CONFIG_PATH
        # above / vault.key), otherwise it is created world/group-readable per
        # the process umask.
        from wth_ai.workspace import secure_file
        secure_file(USER_ENV_PATH)
        for line in lines:
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ[k] = v
        return USER_ENV_PATH


def _make_secret_resolver() -> Any:
    """Return a resolver that turns ``vault:<name>`` refs / env vars into values.

    The vault is opened lazily and never created here: resolving against a
    missing vault simply returns the env fallback.
    """
    from wth_ai.vault import SecretVault, is_vault_ref

    vault = SecretVault()

    def resolve(raw_value: Any, env_key: str) -> str | None:
        if is_vault_ref(raw_value):
            return vault.resolve(raw_value) or os.environ.get(env_key)
        return raw_value or os.environ.get(env_key)

    return resolve


def _inject_secret_env(secrets: SecretsConfig, bots: BotConfig) -> None:
    """Expose resolved secrets to in-process env for subprocesses (no disk write)."""
    mapping = {
        "ROUTER9_API_KEY": secrets.router9_api_key,
        "9ROUTER_API_KEY": secrets.router9_api_key,
        "OPENROUTER_API_KEY": secrets.openrouter_api_key,
        "OPENAI_API_KEY": secrets.openai_api_key,
        "ANTHROPIC_API_KEY": secrets.anthropic_api_key,
        "GOOGLE_API_KEY": secrets.google_api_key,
        "GROQ_API_KEY": secrets.groq_api_key,
        "TELEGRAM_BOT_TOKEN": bots.telegram_token,
        "DISCORD_BOT_TOKEN": bots.discord_token,
    }
    for env_key, value in mapping.items():
        if value and not os.environ.get(env_key):
            os.environ[env_key] = value


def _parse_integrations(raw: dict[str, Any]) -> IntegrationsConfig:
    raw = raw or {}
    tg_raw = raw.get("telegram", {}) or {}
    dc_raw = raw.get("discord", {}) or {}
    wh_raw = raw.get("webhook", {}) or {}

    def _notify(d: dict[str, Any]) -> dict[str, bool]:
        merged = dict(DEFAULT_NOTIFY_RULES)
        merged.update({k: bool(v) for k, v in (d.get("notify", {}) or {}).items()})
        return merged

    telegram = TelegramIntegration(
        enabled=bool(tg_raw.get("enabled", False)),
        bot_token_ref=tg_raw.get("bot_token_ref"),
        allowed_chat_ids=[str(x) for x in tg_raw.get("allowed_chat_ids", [])],
        owner_user_ids=[str(x) for x in tg_raw.get("owner_user_ids", [])],
        notify=_notify(tg_raw),
    )
    discord = DiscordIntegration(
        enabled=bool(dc_raw.get("enabled", False)),
        bot_token_ref=dc_raw.get("bot_token_ref"),
        guild_id=str(dc_raw["guild_id"]) if dc_raw.get("guild_id") else None,
        allowed_channel_ids=[str(x) for x in dc_raw.get("allowed_channel_ids", [])],
        role_permissions=(
            {k: list(v) for k, v in dc_raw["role_permissions"].items()}
            if dc_raw.get("role_permissions")
            else {k: list(v) for k, v in DEFAULT_DISCORD_ROLE_PERMISSIONS.items()}
        ),
        notify=_notify(dc_raw),
    )
    webhook = WebhookIntegration(
        enabled=bool(wh_raw.get("enabled", False)),
        url=wh_raw.get("url"),
        notify=_notify(wh_raw),
    )
    return IntegrationsConfig(telegram=telegram, discord=discord, webhook=webhook)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def is_setup_complete() -> bool:
    if os.environ.get("WTH_SKIP_SETUP") == "1":
        return True
    if USER_CONFIG_PATH.exists():
        raw = yaml.safe_load(USER_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return bool(raw.get("setup_complete"))
    return False
