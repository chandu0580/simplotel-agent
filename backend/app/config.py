from dataclasses import dataclass
from functools import lru_cache
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    model: str
    effort: str
    refusal_fallback: str
    llm_timeout_seconds: float
    llm_max_retries: int
    ai_enabled: bool
    cors_origins: list[str]
    log_level: str


@lru_cache
def get_settings() -> Settings:
    api_key = os.getenv("ANTHROPIC_API_KEY") or None
    return Settings(
        anthropic_api_key=api_key,
        model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5"),
        # Short FAQ answers and a single tool decision do not need deep reasoning;
        # "low" keeps latency chat-like. Raise it if evals show a quality gap.
        effort=os.getenv("ANTHROPIC_EFFORT", "low"),
        # "default" = Anthropic's server-side refusal fallback (one re-run, no client loop); "none" disables.
        refusal_fallback=os.getenv("ANTHROPIC_REFUSAL_FALLBACK", "default").lower(),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "20")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "1")),
        ai_enabled=os.getenv("AI_ENABLED", "true").lower() == "true" and api_key is not None,
        cors_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()],
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
