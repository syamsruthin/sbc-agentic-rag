"""Layered config loader: merge order, overrides, safety checks and bot-profile rules."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from sbc_core.config import (
    BotName,
    ConfigError,
    Env,
    EnvSettings,
    config_hash,
    deep_merge,
    load_config,
    load_settings,
)


def _copy_config(src: Path, tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(src, target)
    return target


def _edit(path: Path, update: dict[str, Any]) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    path.write_text(yaml.safe_dump(deep_merge(data, update)), encoding="utf-8")


def _env(**values: str) -> EnvSettings:
    # The conftest clears the environment and turns off the .env file, so tests see only these values.
    return EnvSettings.model_validate(values)


@pytest.mark.parametrize("bot", list(BotName))
@pytest.mark.parametrize("env", list(Env))
def test_repo_config_loads_for_every_bot_and_env(config_dir: Path, bot: BotName, env: Env) -> None:
    config = load_config(bot, env, config_dir)
    assert config.bot.name is bot
    assert config.env is env


def test_layers_merge_in_order(tmp_path: Path, config_dir: Path) -> None:
    root = _copy_config(config_dir, tmp_path)
    _edit(root / "base.yaml", {"retrieval": {"keep_per_plan": 4, "prefetch_limit": 30}})
    _edit(root / "bots" / "broker.yaml", {"retrieval": {"keep_per_plan": 5}})
    _edit(root / "env" / "dev.yaml", {"retrieval": {"keep_per_plan": 7}})

    assert load_config("broker", "dev", root).retrieval.keep_per_plan == 7
    assert load_config("broker", "local", root).retrieval.keep_per_plan == 5
    assert load_config("public", "local", root).retrieval.keep_per_plan == 4
    assert load_config("broker", "dev", root).retrieval.prefetch_limit == 30


def test_environment_variable_overrides_yaml(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SBC_RETRIEVAL__RERANK_ENABLED", "false")
    config = load_config("broker", "local", config_dir)
    assert config.retrieval.rerank_enabled is False
    assert config.retrieval.rrf_k == 60


def test_misspelled_override_is_rejected(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SBC_RETRIEVAL__RERANK_ENABLD", "false")
    with pytest.raises(ConfigError, match="rerank_enabld"):
        load_config("broker", "local", config_dir)


def test_unknown_yaml_key_is_rejected(tmp_path: Path, config_dir: Path) -> None:
    root = _copy_config(config_dir, tmp_path)
    _edit(root / "base.yaml", {"retrieval": {"top_k": 5}})
    with pytest.raises(ConfigError, match="top_k"):
        load_config("broker", "local", root)


@pytest.mark.parametrize(
    "key", ["openrouter_api_key", "jwt_secret", "upstash_token", "db_password", "sentry_dsn"]
)
def test_secret_like_yaml_key_is_rejected(tmp_path: Path, config_dir: Path, key: str) -> None:
    root = _copy_config(config_dir, tmp_path)
    _edit(root / "env" / "local.yaml", {"retrieval": {key: "value"}})
    with pytest.raises(ConfigError, match="environment variable"):
        load_config("broker", "local", root)


def test_env_cannot_be_set_in_yaml(tmp_path: Path, config_dir: Path) -> None:
    root = _copy_config(config_dir, tmp_path)
    _edit(root / "base.yaml", {"env": "prd"})
    with pytest.raises(ConfigError, match="APP_ENV"):
        load_config("broker", "local", root)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"allow_discovery": True}, "broker-only"),
        ({"allow_cross_state": True}, "broker-only"),
        ({"csv_export": True}, "broker-only"),
        ({"compare_max_plans": 6}, "at most 3"),
        ({"plan_recommendation_policy": "rank_by_stated_criteria"}, "tradeoffs_only"),
        ({"plan_scope": {"markets": ["Individual", "Small Group"]}}, r"exactly \[Individual\]"),
        ({"auth": "cognito"}, "anonymous"),
        ({"readability_max_grade": None}, "readability_max_grade"),
        ({"rate_limits": {"per_ip_per_day": None}}, "per_ip_per_day"),
    ],
)
def test_public_profile_rejects_broker_settings(
    tmp_path: Path, config_dir: Path, update: dict[str, Any], message: str
) -> None:
    root = _copy_config(config_dir, tmp_path)
    _edit(root / "bots" / "public.yaml", {"bot": update})
    with pytest.raises(ConfigError, match=message):
        load_config("public", "local", root)


def test_broker_requires_cognito_auth(tmp_path: Path, config_dir: Path) -> None:
    root = _copy_config(config_dir, tmp_path)
    _edit(root / "bots" / "broker.yaml", {"bot": {"auth": "anonymous"}})
    with pytest.raises(ConfigError, match="cognito"):
        load_config("broker", "local", root)


def test_supabase_auth_is_no_longer_accepted(tmp_path: Path, config_dir: Path) -> None:
    root = _copy_config(config_dir, tmp_path)
    _edit(root / "bots" / "broker.yaml", {"bot": {"auth": "supabase"}})
    with pytest.raises(ConfigError, match="auth"):
        load_config("broker", "local", root)


# ------------------------------------------------------------------ portfolio limits


def test_portfolio_limits_are_off_unless_an_environment_sets_them(config_dir: Path) -> None:
    local = load_config("broker", "local", config_dir).portfolio
    assert not local.enabled
    assert local.global_daily_llm_turns is None
    dev = load_config("public", "dev", config_dir).portfolio
    assert dev.global_daily_llm_turns == 300
    assert (dev.demo_rate_limits.per_minute, dev.demo_rate_limits.per_day) == (15, 100)
    assert dev.demo_history_ttl_hours == 24


def test_demo_user_ids_come_from_the_environment(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SBC_PORTFOLIO__DEMO_USER_IDS", '["11111111-2222-3333-4444-555555555555"]')
    portfolio = load_config("broker", "dev", config_dir).portfolio
    assert portfolio.demo_user_ids == ("11111111-2222-3333-4444-555555555555",)
    assert portfolio.enabled


@pytest.mark.parametrize(
    "update",
    [
        {"global_daily_llm_turns": 0},
        {"demo_history_ttl_hours": 0},
        {"demo_rate_limits": {"per_minute": 0, "per_day": 10}},
        {"unknown_limit": 5},
    ],
)
def test_invalid_portfolio_settings_are_rejected(
    tmp_path: Path, config_dir: Path, update: dict[str, Any]
) -> None:
    root = _copy_config(config_dir, tmp_path)
    _edit(root / "env" / "dev.yaml", {"portfolio": update})
    with pytest.raises(ConfigError, match="portfolio"):
        load_config("broker", "dev", root)


def test_unknown_tool_is_rejected(tmp_path: Path, config_dir: Path) -> None:
    root = _copy_config(config_dir, tmp_path)
    _edit(
        root / "bots" / "broker.yaml",
        {"bot": {"tools": ["get_plan_facts", "ask_clarifying_question", "web_fetch"]}},
    )
    with pytest.raises(ConfigError, match="web_fetch"):
        load_config("broker", "local", root)


def test_required_tool_cannot_be_dropped(tmp_path: Path, config_dir: Path) -> None:
    root = _copy_config(config_dir, tmp_path)
    _edit(root / "bots" / "public.yaml", {"bot": {"tools": ["get_plan_facts", "find_plans"]}})
    with pytest.raises(ConfigError, match="ask_clarifying_question"):
        load_config("public", "local", root)


def test_bot_file_must_declare_its_own_bot(tmp_path: Path, config_dir: Path) -> None:
    root = _copy_config(config_dir, tmp_path)
    shutil.copy(root / "bots" / "public.yaml", root / "bots" / "broker.yaml")
    with pytest.raises(ConfigError, match=r"bot\.name=public"):
        load_config("broker", "local", root)


@pytest.mark.parametrize(("bot", "env"), [("admin", "local"), ("broker", "staging")])
def test_unknown_bot_or_env_is_rejected(config_dir: Path, bot: str, env: str) -> None:
    with pytest.raises(ConfigError):
        load_config(bot, env, config_dir)


def test_missing_config_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config("broker", "local", tmp_path)


def test_config_hash_is_stable_and_tracks_changes(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broker = config_hash(load_config("broker", "local", config_dir))
    assert broker == config_hash(load_config("broker", "local", config_dir))
    assert len(broker) == 12
    assert broker != config_hash(load_config("public", "local", config_dir))
    monkeypatch.setenv("SBC_RETRIEVAL__KEEP_PER_PLAN", "5")
    assert broker != config_hash(load_config("broker", "local", config_dir))


def test_local_settings_need_no_secrets(config_dir: Path) -> None:
    settings = load_settings("public", config_dir, _env())
    assert settings.env.app_env is Env.LOCAL
    assert settings.config.bot.name is BotName.PUBLIC
    assert settings.config_hash == config_hash(settings.config)


def test_dev_settings_fail_fast_and_name_only_missing_variables(config_dir: Path) -> None:
    env = _env(app_env="dev", langfuse_secret_key="super-secret-value")  # noqa: S106 - a fake value
    with pytest.raises(ConfigError) as excinfo:
        load_settings("broker", config_dir, env)
    message = str(excinfo.value)
    missing = message.split(": ", 1)[1].split(", ")
    assert "OPENROUTER_API_KEY_BROKER" in missing
    assert {
        "COGNITO_USER_POOL_ID",
        "COGNITO_APP_CLIENT_ID",
        "DDB_TABLE_CHECKPOINTS",
        "S3_DATA_BUCKET",
    } <= set(missing)
    assert "LANGFUSE_SECRET_KEY" not in missing
    assert "TURNSTILE_SECRET_KEY" not in missing
    assert "super-secret-value" not in message


def test_public_bot_needs_turnstile_and_session_secret(config_dir: Path) -> None:
    missing = _env(app_env="prd").missing_for(BotName.PUBLIC)
    assert {"OPENROUTER_API_KEY_PUBLIC", "TURNSTILE_SECRET_KEY", "SESSION_JWT_SECRET"} <= set(missing)
    assert "OPENROUTER_API_KEY_BROKER" not in missing
    assert "COGNITO_USER_POOL_ID" not in missing
    assert {"DDB_TABLE_APP", "DDB_TABLE_AUDIT", "DDB_TABLE_CHECKPOINTS", "S3_DATA_BUCKET"} <= set(missing)


def test_supabase_and_upstash_settings_are_gone() -> None:
    names = set(EnvSettings.model_fields)
    assert not {n for n in names if n.startswith(("supabase_", "upstash_"))}
    assert "database_url" not in names


def test_langfuse_is_optional_and_needs_both_keys() -> None:
    assert not _env().langfuse_enabled
    assert not _env(langfuse_public_key="pk-lf-example").langfuse_enabled
    both = _env(langfuse_public_key="pk-lf-example", langfuse_secret_key="sk-lf-example")  # noqa: S106 - fake
    assert both.langfuse_enabled
    missing = _env(app_env="dev").missing_for(BotName.BROKER)
    assert not {n for n in missing if n.startswith("LANGFUSE")}


@pytest.mark.parametrize("bot", list(BotName))
def test_dev_settings_load_when_every_secret_is_present(config_dir: Path, bot: BotName) -> None:
    needed = _env(app_env="dev").missing_for(bot)
    env = _env(app_env="dev", **{name.lower(): "set" for name in needed})
    settings = load_settings(bot, config_dir, env)
    assert settings.env.app_env is Env.DEV


def test_empty_secret_counts_as_missing() -> None:
    env = _env(app_env="dev", openrouter_api_key_broker="")
    assert "OPENROUTER_API_KEY_BROKER" in env.missing_for(BotName.BROKER)


def test_secrets_are_masked_in_repr() -> None:
    env = _env(openrouter_api_key_public="sk-or-example-123")
    assert "sk-or-example-123" not in repr(env)
    assert "sk-or-example-123" not in str(env.model_dump())


def test_env_settings_read_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prd")
    monkeypatch.setenv("FACTS_BUNDLE_PATH", "bundles/facts-2027.1.sqlite")
    env = EnvSettings(_env_file=None)
    assert env.app_env is Env.PRD
    assert env.facts_bundle_path == "bundles/facts-2027.1.sqlite"


def test_deep_merge_leaves_inputs_unchanged() -> None:
    base = {"a": {"b": 1, "c": 2}, "d": [1]}
    override = {"a": {"b": 3}, "d": [2]}
    assert deep_merge(base, override) == {"a": {"b": 3, "c": 2}, "d": [2]}
    assert base == {"a": {"b": 1, "c": 2}, "d": [1]}


@pytest.mark.parametrize("name", ["LANGFUSE_BASE_URL", "LANGFUSE_HOST"])
def test_the_langfuse_region_comes_from_either_variable(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """LANGFUSE_BASE_URL is the name Langfuse shows with a project's keys; LANGFUSE_HOST is the older one."""
    for other in ("LANGFUSE_BASE_URL", "LANGFUSE_HOST"):
        monkeypatch.delenv(other, raising=False)
    assert EnvSettings(_env_file=None).langfuse_url == "https://cloud.langfuse.com"
    monkeypatch.setenv(name, "https://us.cloud.langfuse.com")
    assert EnvSettings(_env_file=None).langfuse_url == "https://us.cloud.langfuse.com"
