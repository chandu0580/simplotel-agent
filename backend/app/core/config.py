"""Centralised, environment-aware configuration.

All settings come from environment variables (optionally a local `.env`). `Settings.validate()`
enforces rules that must hold in production, so a misconfigured deployment fails at startup
instead of running insecurely.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import os
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ENVIRONMENTS = ("development", "test", "production")


class ConfigError(ValueError):
    """Configuration is invalid for the selected environment."""


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    return int(raw) if raw not in (None, "") else default


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"

    # LLM
    llm_provider: str = "anthropic"  # anthropic | none
    anthropic_api_key: str | None = field(default=None, repr=False)
    model_primary: str = "claude-opus-5"
    model_fast: str = "claude-opus-5"  # set to a cheaper model only after evals show equal quality
    effort: str = "low"
    refusal_fallback: str = "default"  # default | none
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 1
    llm_max_tokens: int = 16000
    ai_enabled: bool = True

    # HTTP
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    trust_proxy_headers: bool = False
    expose_api_docs: bool = True

    # Logging / telemetry
    log_level: str = "INFO"
    log_format: str = "text"  # text | json
    metrics_enabled: bool = True

    # Data
    data_dir: Path = DEFAULT_DATA_DIR

    # Auth (admin API). Guest chat is intentionally unauthenticated.
    auth_mode: str = "disabled"  # disabled | static_token (development only)
    admin_api_tokens: str = field(default="", repr=False)

    # Rate limits (requests per minute)
    rate_limit_enabled: bool = True
    rate_limit_ip_per_minute: int = 60
    rate_limit_conversation_per_minute: int = 20
    rate_limit_hotel_per_minute: int = 1200

    # Caching
    knowledge_cache_ttl_seconds: int = 300
    availability_cache_ttl_seconds: int = 15

    # Conversations
    conversation_ttl_seconds: int = 24 * 3600
    conversation_max_messages: int = 40
    conversation_context_window: int = 12
    conversation_max_active: int = 50_000

    # Reservation integration resilience
    reservation_timeout_seconds: float = 5.0
    reservation_read_retries: int = 2
    circuit_breaker_failures: int = 5
    circuit_breaker_reset_seconds: float = 30.0

    # Feature flags (global defaults; tenants can override in tenants.json)
    feature_flags: Mapping[str, bool] = field(default_factory=dict)

    @property
    def llm_configured(self) -> bool:
        return self.ai_enabled and self.llm_provider == "anthropic" and bool(self.anthropic_api_key)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        if env is None:
            load_dotenv()
            env = os.environ
        flags = {
            key.removeprefix("FEATURE_").lower(): _bool(value, False)
            for key, value in env.items()
            if key.startswith("FEATURE_")
        }
        return cls(
            app_env=env.get("APP_ENV", "development").lower(),
            llm_provider=env.get("LLM_PROVIDER", "anthropic").lower(),
            anthropic_api_key=env.get("ANTHROPIC_API_KEY") or None,
            model_primary=env.get("ANTHROPIC_MODEL", "claude-opus-5"),
            model_fast=env.get("ANTHROPIC_MODEL_FAST") or env.get("ANTHROPIC_MODEL", "claude-opus-5"),
            effort=env.get("ANTHROPIC_EFFORT", "low"),
            refusal_fallback=env.get("ANTHROPIC_REFUSAL_FALLBACK", "default").lower(),
            llm_timeout_seconds=_float(env, "LLM_TIMEOUT_SECONDS", 20.0),
            llm_max_retries=_int(env, "LLM_MAX_RETRIES", 1),
            llm_max_tokens=_int(env, "LLM_MAX_TOKENS", 16000),
            ai_enabled=_bool(env.get("AI_ENABLED"), True),
            cors_origins=tuple(o.strip() for o in env.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()),
            trust_proxy_headers=_bool(env.get("TRUST_PROXY_HEADERS"), False),
            expose_api_docs=_bool(env.get("EXPOSE_API_DOCS"), env.get("APP_ENV", "development").lower() != "production"),
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
            log_format=env.get("LOG_FORMAT", "json" if env.get("APP_ENV") == "production" else "text").lower(),
            metrics_enabled=_bool(env.get("METRICS_ENABLED"), True),
            data_dir=Path(env["DATA_DIR"]) if env.get("DATA_DIR") else DEFAULT_DATA_DIR,
            auth_mode=env.get("AUTH_MODE", "disabled").lower(),
            admin_api_tokens=env.get("ADMIN_API_TOKENS", ""),
            rate_limit_enabled=_bool(env.get("RATE_LIMIT_ENABLED"), True),
            rate_limit_ip_per_minute=_int(env, "RATE_LIMIT_IP_PER_MINUTE", 60),
            rate_limit_conversation_per_minute=_int(env, "RATE_LIMIT_CONVERSATION_PER_MINUTE", 20),
            rate_limit_hotel_per_minute=_int(env, "RATE_LIMIT_HOTEL_PER_MINUTE", 1200),
            knowledge_cache_ttl_seconds=_int(env, "KNOWLEDGE_CACHE_TTL_SECONDS", 300),
            availability_cache_ttl_seconds=_int(env, "AVAILABILITY_CACHE_TTL_SECONDS", 15),
            conversation_ttl_seconds=_int(env, "CONVERSATION_TTL_SECONDS", 24 * 3600),
            conversation_max_messages=_int(env, "CONVERSATION_MAX_MESSAGES", 40),
            conversation_context_window=_int(env, "CONVERSATION_CONTEXT_WINDOW", 12),
            conversation_max_active=_int(env, "CONVERSATION_MAX_ACTIVE", 50_000),
            reservation_timeout_seconds=_float(env, "RESERVATION_TIMEOUT_SECONDS", 5.0),
            reservation_read_retries=_int(env, "RESERVATION_READ_RETRIES", 2),
            circuit_breaker_failures=_int(env, "CIRCUIT_BREAKER_FAILURES", 5),
            circuit_breaker_reset_seconds=_float(env, "CIRCUIT_BREAKER_RESET_SECONDS", 30.0),
            feature_flags=flags,
        ).validate()

    @classmethod
    def for_tests(cls, **overrides) -> "Settings":
        base = cls(app_env="test", llm_provider="none", ai_enabled=False, rate_limit_enabled=False, log_level="WARNING")
        return replace(base, **overrides).validate()

    def validate(self) -> "Settings":
        errors = []
        if self.app_env not in ENVIRONMENTS:
            errors.append(f"APP_ENV must be one of {ENVIRONMENTS}")
        if self.llm_provider not in ("anthropic", "none"):
            errors.append("LLM_PROVIDER must be 'anthropic' or 'none'")
        if self.refusal_fallback not in ("default", "none"):
            errors.append("ANTHROPIC_REFUSAL_FALLBACK must be 'default' or 'none'")
        if self.effort not in ("low", "medium", "high", "xhigh", "max"):
            errors.append("ANTHROPIC_EFFORT must be low|medium|high|xhigh|max")
        if self.log_format not in ("text", "json"):
            errors.append("LOG_FORMAT must be 'text' or 'json'")
        if self.auth_mode not in ("disabled", "static_token"):
            errors.append("AUTH_MODE must be 'disabled' or 'static_token'")
        if self.app_env == "production":
            if self.auth_mode == "static_token":
                errors.append("AUTH_MODE=static_token is for local development only; use an OIDC gateway in production")
            if any(o == "*" or "localhost" in o or "127.0.0.1" in o for o in self.cors_origins):
                errors.append("CORS_ORIGINS must list real hotel origins in production (no '*' or localhost)")
            if self.log_format != "json":
                errors.append("LOG_FORMAT must be 'json' in production")
            if not self.rate_limit_enabled:
                errors.append("RATE_LIMIT_ENABLED cannot be false in production")
        if errors:
            raise ConfigError("; ".join(errors))
        return self

    def secret_values(self) -> list[str]:
        """Values that must never appear in logs or responses."""
        values = [self.anthropic_api_key or ""]
        values += [part.split("=", 1)[0] for part in self.admin_api_tokens.split(",") if "=" in part]
        return [v for v in values if len(v) >= 8]
