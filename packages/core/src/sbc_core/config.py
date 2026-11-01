"""Configuration loading for both bots (PLAN.md sections 2.2 and 10).

Behavior settings live in YAML under ``config/`` and are versioned with the code. They are merged in this
order, later layers winning: ``base.yaml`` -> ``bots/<bot>.yaml`` -> ``env/<env>.yaml`` -> ``SBC_*``
environment variables (nested keys use ``__``, e.g. ``SBC_RETRIEVAL__RERANK_ENABLED=false``).

Deploy-time values and secrets come only from environment variables (Doppler in shared environments) and are
never read from YAML. In ``dev`` and ``prd`` every secret the bot needs must be present, so a misconfigured
deployment fails at startup instead of on the first request.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class ConfigError(Exception):
    """Raised when configuration is missing, malformed or unsafe. Messages never contain secret values."""


class Env(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    PRD = "prd"


class BotName(StrEnum):
    BROKER = "broker"
    PUBLIC = "public"


KNOWN_TOOLS = frozenset(
    {
        "find_plans",
        "get_plan_facts",
        "search_sbc_text",
        "compare_plans",
        "rank_plans",
        "estimate_cost",
        "ask_clarifying_question",
        "glossary_lookup",
    }
)
REQUIRED_TOOLS = frozenset({"get_plan_facts", "ask_clarifying_question"})

# A YAML key that looks like it holds a secret is rejected outright: secrets come from the environment only.
_SECRET_KEY_RE = re.compile(r"(api_key|secret|token|password|dsn)$", re.IGNORECASE)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelRole(_Model):
    model: str = Field(min_length=1)
    effort: Literal["low", "medium", "high"] | None = None
    fallbacks: tuple[str, ...] = ()
    dimensions: int | None = Field(default=None, gt=0)


class Models(_Model):
    classify: ModelRole
    agent: ModelRole
    judge: ModelRole
    embeddings: ModelRole
    rerank: ModelRole


class TurnBudget(_Model):
    max_llm_calls: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    max_identical_tool_calls: int = Field(gt=0)
    recursion_limit: int = Field(gt=0)
    wall_clock_seconds: float = Field(gt=0)
    max_input_tokens: int = Field(gt=0)


class Retrieval(_Model):
    prefetch_limit: int = Field(gt=0)
    rrf_k: int = Field(gt=0)
    rerank_candidates: int = Field(gt=0)
    keep_per_plan: int = Field(gt=0)
    rerank_enabled: bool
    max_plans_per_search: int = Field(gt=0)


class SearchIndex(_Model):
    collection_prefix: str = Field(pattern=r"^[a-z]+$")


class PlanScope(_Model):
    markets: tuple[str, ...] = ()
    """Markets this bot may see; empty means every market."""
    exclude_plan_statuses: tuple[str, ...] = ()
    exclude_exchange: tuple[str, ...] = ()


class RateLimits(_Model):
    per_minute: int = Field(gt=0)
    per_day: int = Field(gt=0)
    per_ip_per_day: int | None = Field(default=None, gt=0)


class BotProfile(_Model):
    name: BotName
    auth: Literal["supabase", "anonymous"]
    plan_scope: PlanScope
    tools: tuple[str, ...]
    allow_discovery: bool
    allow_cross_state: bool
    csv_export: bool
    compare_max_plans: int = Field(ge=2)
    plan_recommendation_policy: Literal["rank_by_stated_criteria", "tradeoffs_only"]
    readability_max_grade: float | None = None
    rate_limits: RateLimits
    retention_days: int = Field(gt=0)
    max_cost_per_turn_usd: float = Field(gt=0)

    @model_validator(mode="after")
    def _check_profile(self) -> Self:
        unknown = set(self.tools) - KNOWN_TOOLS
        if unknown:
            raise ValueError(f"unknown tools: {sorted(unknown)}")
        missing = REQUIRED_TOOLS - set(self.tools)
        if missing:
            raise ValueError(f"required tools missing: {sorted(missing)}")
        if self.name is BotName.PUBLIC:
            self._check_public()
        elif self.auth != "supabase":
            raise ValueError("the broker bot must use supabase auth")
        return self

    def _check_public(self) -> None:
        problems = []
        if self.auth != "anonymous":
            problems.append("auth must be anonymous")
        if self.allow_discovery or self.allow_cross_state or self.csv_export:
            problems.append("discovery, cross-state search and CSV export are broker-only")
        if self.compare_max_plans > 3:
            problems.append("compare_max_plans must be at most 3")
        if self.plan_recommendation_policy != "tradeoffs_only":
            problems.append("plan_recommendation_policy must be tradeoffs_only")
        if self.plan_scope.markets != ("Individual",):
            problems.append("plan_scope.markets must be exactly [Individual]")
        if self.readability_max_grade is None:
            problems.append("readability_max_grade is required")
        if self.rate_limits.per_ip_per_day is None:
            problems.append("rate_limits.per_ip_per_day is required")
        if problems:
            raise ValueError("public profile: " + "; ".join(problems))


class AppConfig(BaseSettings):
    """Behavior settings: merged YAML layers, overridable through ``SBC_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="SBC_", env_nested_delimiter="__", extra="forbid", frozen=True, env_file=None
    )

    env: Env
    bot: BotProfile
    models: Models
    budget: TurnBudget
    retrieval: Retrieval
    search_index: SearchIndex

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Environment variables win over the merged YAML passed in as init kwargs.
        return (env_settings, init_settings)


class EnvSettings(BaseSettings):
    """Deploy-time values and secrets, read from environment variables (or a local ``.env``) only."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    app_env: Env = Env.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    facts_bundle_path: str = "bundles/facts.sqlite"

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key_broker: SecretStr | None = None
    openrouter_api_key_public: SecretStr | None = None

    qdrant_url: str | None = None
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str | None = None

    supabase_url: str | None = None
    supabase_anon_key: SecretStr | None = None
    supabase_service_role_key: SecretStr | None = None
    supabase_jwt_secret: SecretStr | None = None
    database_url: SecretStr | None = None

    upstash_redis_rest_url: str | None = None
    upstash_redis_rest_token: SecretStr | None = None

    turnstile_secret_key: SecretStr | None = None
    session_jwt_secret: SecretStr | None = None

    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    sentry_dsn: SecretStr | None = None

    def openrouter_key_for(self, bot: BotName) -> SecretStr | None:
        return self.openrouter_api_key_broker if bot is BotName.BROKER else self.openrouter_api_key_public

    def missing_for(self, bot: BotName) -> list[str]:
        """Names (never values) of the environment variables this bot needs in dev and prd but lacks."""
        required = [
            f"openrouter_api_key_{bot.value}",
            "qdrant_url",
            "qdrant_api_key",
            "qdrant_collection",
            "database_url",
            "upstash_redis_rest_url",
            "upstash_redis_rest_token",
            "langfuse_public_key",
            "langfuse_secret_key",
        ]
        if bot is BotName.BROKER:
            required += ["supabase_url", "supabase_jwt_secret"]
        else:
            required += ["turnstile_secret_key", "session_jwt_secret"]
        return [name.upper() for name in required if not _is_set(getattr(self, name))]


def _is_set(value: object) -> bool:
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    return bool(value)


@dataclass(frozen=True)
class Settings:
    config: AppConfig
    env: EnvSettings
    config_hash: str


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``; non-dict values are replaced."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    _reject_secret_keys(data, path)
    return data


def _reject_secret_keys(data: dict[str, Any], path: Path, prefix: str = "") -> None:
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if _SECRET_KEY_RE.search(str(key)):
            raise ConfigError(f"{path}: '{dotted}' looks like a secret; set it as an environment variable")
        if isinstance(value, dict):
            _reject_secret_keys(value, path, f"{dotted}.")


def config_hash(config: AppConfig) -> str:
    """Short, stable fingerprint of the effective config, recorded on every trace."""
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def load_config(bot: BotName | str, env: Env | str, config_dir: Path | str = "config") -> AppConfig:
    """Merge the YAML layers for ``bot`` in ``env`` and apply ``SBC_*`` overrides."""
    try:
        bot, env = BotName(bot), Env(env)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    root = Path(config_dir)
    merged: dict[str, Any] = {}
    for path in (root / "base.yaml", root / "bots" / f"{bot.value}.yaml", root / "env" / f"{env.value}.yaml"):
        merged = deep_merge(merged, _read_yaml(path))
    if "env" in merged:
        raise ConfigError("'env' is chosen by APP_ENV and must not be set in YAML")
    merged["env"] = env.value
    try:
        config = AppConfig(**merged)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration for bot={bot.value} env={env.value}:\n{exc}") from None
    if config.bot.name is not bot:
        raise ConfigError(f"bots/{bot.value}.yaml declares bot.name={config.bot.name.value}")
    return config


def load_settings(
    bot: BotName | str, config_dir: Path | str = "config", env_settings: EnvSettings | None = None
) -> Settings:
    """Load everything a bot process needs and fail fast on missing secrets in dev and prd."""
    env = env_settings if env_settings is not None else EnvSettings()
    config = load_config(bot, env.app_env, config_dir)
    if env.app_env is not Env.LOCAL:
        missing = env.missing_for(config.bot.name)
        if missing:
            raise ConfigError(
                f"missing environment variables for bot={config.bot.name.value} env={env.app_env.value}: "
                + ", ".join(missing)
            )
    return Settings(config=config, env=env, config_hash=config_hash(config))
