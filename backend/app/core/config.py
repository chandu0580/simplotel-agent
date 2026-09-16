"""Centralised, environment-aware configuration.

All settings come from environment variables (optionally a local `.env`). `Settings.validate()`
enforces rules that must hold in production, so a misconfigured deployment fails at startup
instead of running insecurely.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import os
from pathlib import Path
from urllib.parse import urlsplit

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

    # LLM. Default runtime provider: GLM over the OpenAI-compatible protocol. Anthropic stays selectable.
    llm_provider: str = "glm"  # glm | anthropic | mock (non-production) | none
    llm_api_key: str | None = field(default=None, repr=False)
    llm_base_url: str | None = None
    anthropic_api_key: str | None = field(default=None, repr=False)
    anthropic_base_url: str | None = None
    model_primary: str = "glm-5.2"
    model_fast: str = "glm-5.2"  # set to a cheaper model only after evals show equal quality
    effort: str = "low"  # Anthropic only
    refusal_fallback: str = "default"  # Anthropic only: default | none
    mock_llm_latency_ms: int = 1500
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 1
    llm_max_tokens: int = 16000
    ai_enabled: bool = True

    # HTTP
    # Worker threads for sync endpoints. Guest turns spend most of their time waiting on the model (I/O), so
    # this, not CPU, caps concurrent AI turns per process (perf/load_results.md). Each thread costs memory only.
    worker_threads: int = 150
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    trust_proxy_headers: bool = False
    expose_api_docs: bool = True

    # Logging / telemetry
    log_level: str = "INFO"
    log_format: str = "text"  # text | json
    metrics_enabled: bool = True

    # Data
    data_dir: Path = DEFAULT_DATA_DIR

    # Shared state. memory = one process (default); redis = several replicas share conversations,
    # rate limits, idempotency results and locks. DATABASE_URL enables the PostgreSQL audit store.
    state_backend: str = "memory"  # memory | redis
    redis_url: str | None = field(default=None, repr=False)
    redis_key_prefix: str = "sa"
    database_url: str | None = field(default=None, repr=False)
    audit_retention_days: int = 365

    # Auth (admin API). Guest chat is intentionally unauthenticated.
    auth_mode: str = "disabled"  # disabled | static_token (development only)
    admin_api_tokens: str = field(default="", repr=False)

    # Rate limits (requests per minute)
    rate_limit_enabled: bool = True
    rate_limit_ip_per_minute: int = 60
    rate_limit_conversation_per_minute: int = 20
    rate_limit_hotel_per_minute: int = 1200
    rate_limit_tenant_per_minute: int = 3000
    rate_limit_ip_burst: int = 30  # per RATE_LIMIT_BURST_WINDOW_SECONDS; generous: guests on one hotel Wi-Fi share an IP
    rate_limit_burst_window_seconds: int = 10

    # Caching
    knowledge_cache_ttl_seconds: int = 300
    availability_cache_ttl_seconds: int = 15

    # Conversations
    conversation_ttl_seconds: int = 24 * 3600
    conversation_max_messages: int = 40
    conversation_context_window: int = 12
    conversation_max_active: int = 50_000
    conversation_lock_wait_seconds: float = 30.0
    pii_mask_contact_details: bool = True  # card numbers are always masked
    conversation_lock_lease_seconds: float = 120.0

    # Reservation integration resilience
    reservation_timeout_seconds: float = 5.0
    reservation_read_retries: int = 2
    circuit_breaker_failures: int = 5
    circuit_breaker_reset_seconds: float = 30.0

    # Feature flags (global defaults; tenants can override in tenants.json)
    feature_flags: Mapping[str, bool] = field(default_factory=dict)

    @property
    def llm_configured(self) -> bool:
        if not self.ai_enabled:
            return False
        if self.llm_provider == "glm":
            return bool(self.llm_api_key and self.llm_base_url)
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        return self.llm_provider == "mock"

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
            llm_provider=(provider := env.get("LLM_PROVIDER", "glm").lower()),
            llm_api_key=env.get("LLM_API_KEY") or None,
            llm_base_url=env.get("LLM_BASE_URL") or None,
            anthropic_api_key=env.get("ANTHROPIC_API_KEY") or None,
            anthropic_base_url=env.get("ANTHROPIC_BASE_URL") or None,
            model_primary=(primary := env.get("ANTHROPIC_MODEL", "claude-opus-5") if provider == "anthropic" else env.get("LLM_MODEL", "glm-5.2")),
            model_fast=(env.get("ANTHROPIC_MODEL_FAST") if provider == "anthropic" else env.get("LLM_MODEL_FAST")) or primary,
            mock_llm_latency_ms=_int(env, "MOCK_LLM_LATENCY_MS", 1500),
            effort=env.get("ANTHROPIC_EFFORT", "low"),
            refusal_fallback=env.get("ANTHROPIC_REFUSAL_FALLBACK", "default").lower(),
            llm_timeout_seconds=_float(env, "LLM_TIMEOUT_SECONDS", 20.0),
            llm_max_retries=_int(env, "LLM_MAX_RETRIES", 1),
            llm_max_tokens=_int(env, "LLM_MAX_TOKENS", 16000),
            ai_enabled=_bool(env.get("AI_ENABLED"), True),
            worker_threads=_int(env, "WORKER_THREADS", 150),
            cors_origins=tuple(o.strip() for o in env.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()),
            trust_proxy_headers=_bool(env.get("TRUST_PROXY_HEADERS"), False),
            expose_api_docs=_bool(env.get("EXPOSE_API_DOCS"), env.get("APP_ENV", "development").lower() != "production"),
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
            log_format=env.get("LOG_FORMAT", "json" if env.get("APP_ENV") == "production" else "text").lower(),
            metrics_enabled=_bool(env.get("METRICS_ENABLED"), True),
            data_dir=Path(env["DATA_DIR"]) if env.get("DATA_DIR") else DEFAULT_DATA_DIR,
            state_backend=env.get("STATE_BACKEND", "memory").lower(),
            redis_url=env.get("REDIS_URL") or None,
            redis_key_prefix=env.get("REDIS_KEY_PREFIX", "sa"),
            database_url=env.get("DATABASE_URL") or None,
            audit_retention_days=_int(env, "AUDIT_RETENTION_DAYS", 365),
            auth_mode=env.get("AUTH_MODE", "disabled").lower(),
            admin_api_tokens=env.get("ADMIN_API_TOKENS", ""),
            rate_limit_enabled=_bool(env.get("RATE_LIMIT_ENABLED"), True),
            rate_limit_ip_per_minute=_int(env, "RATE_LIMIT_IP_PER_MINUTE", 60),
            rate_limit_conversation_per_minute=_int(env, "RATE_LIMIT_CONVERSATION_PER_MINUTE", 20),
            rate_limit_hotel_per_minute=_int(env, "RATE_LIMIT_HOTEL_PER_MINUTE", 1200),
            rate_limit_tenant_per_minute=_int(env, "RATE_LIMIT_TENANT_PER_MINUTE", 3000),
            rate_limit_ip_burst=_int(env, "RATE_LIMIT_IP_BURST", 30),
            rate_limit_burst_window_seconds=_int(env, "RATE_LIMIT_BURST_WINDOW_SECONDS", 10),
            knowledge_cache_ttl_seconds=_int(env, "KNOWLEDGE_CACHE_TTL_SECONDS", 300),
            availability_cache_ttl_seconds=_int(env, "AVAILABILITY_CACHE_TTL_SECONDS", 15),
            conversation_ttl_seconds=_int(env, "CONVERSATION_TTL_SECONDS", 24 * 3600),
            conversation_max_messages=_int(env, "CONVERSATION_MAX_MESSAGES", 40),
            conversation_context_window=_int(env, "CONVERSATION_CONTEXT_WINDOW", 12),
            conversation_max_active=_int(env, "CONVERSATION_MAX_ACTIVE", 50_000),
            conversation_lock_wait_seconds=_float(env, "CONVERSATION_LOCK_WAIT_SECONDS", 30.0),
            pii_mask_contact_details=_bool(env.get("PII_MASK_CONTACT_DETAILS"), True),
            conversation_lock_lease_seconds=_float(env, "CONVERSATION_LOCK_LEASE_SECONDS", 120.0),
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
        if self.llm_provider not in ("glm", "anthropic", "mock", "none"):
            errors.append("LLM_PROVIDER must be 'glm', 'anthropic', 'mock' or 'none'")
        if self.refusal_fallback not in ("default", "none"):
            errors.append("ANTHROPIC_REFUSAL_FALLBACK must be 'default' or 'none'")
        if self.effort not in ("low", "medium", "high", "xhigh", "max"):
            errors.append("ANTHROPIC_EFFORT must be low|medium|high|xhigh|max")
        if self.log_format not in ("text", "json"):
            errors.append("LOG_FORMAT must be 'text' or 'json'")
        if self.auth_mode not in ("disabled", "static_token"):
            errors.append("AUTH_MODE must be 'disabled' or 'static_token'")
        if not 1 <= self.worker_threads <= 1000:
            errors.append("WORKER_THREADS must be between 1 and 1000")
        if self.state_backend not in ("memory", "redis"):
            errors.append("STATE_BACKEND must be 'memory' or 'redis'")
        if self.state_backend == "redis" and not self.redis_url:
            errors.append("STATE_BACKEND=redis requires REDIS_URL")
        if self.redis_url and not self.redis_url.lower().startswith(("redis://", "rediss://", "unix://")):
            errors.append("REDIS_URL must start with redis://, rediss:// or unix://")
        if self.database_url and not self.database_url.lower().startswith(("postgresql://", "postgres://")):
            errors.append("DATABASE_URL must be a postgresql:// URL")
        if self.conversation_lock_lease_seconds <= self.llm_timeout_seconds * (self.llm_max_retries + 1):
            errors.append("CONVERSATION_LOCK_LEASE_SECONDS must exceed the LLM time budget (LLM_TIMEOUT_SECONDS x (LLM_MAX_RETRIES + 1))")
        if self.app_env == "production":
            # Guest messages and the provider key travel to the LLM endpoint: never over plain HTTP.
            for name, url in (("LLM_BASE_URL", self.llm_base_url), ("ANTHROPIC_BASE_URL", self.anthropic_base_url)):
                if url and not url.lower().startswith("https://"):
                    errors.append(f"{name} must use https:// in production")
            if self.llm_provider == "mock":
                errors.append("LLM_PROVIDER=mock is for load tests and demos only")
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
        values = [self.anthropic_api_key or "", self.llm_api_key or ""]
        values += [part.split("=", 1)[0] for part in self.admin_api_tokens.split(",") if "=" in part]
        values += [urlsplit(url).password or "" for url in (self.redis_url, self.database_url) if url]
        return [v for v in values if len(v) >= 8]
