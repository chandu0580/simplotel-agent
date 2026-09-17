"""Composition root: the one place that picks implementations for each boundary.

Swapping an implementation (Redis rate limiter, database knowledge provider, PMS reservations,
OpenTelemetry trace sink) means changing this file, not the assistant or the API.
"""

from dataclasses import dataclass
from typing import Any

from .assistant.agent import AIAssistant
from .assistant.guardrails import InputGuardrails, OutputGuardrails
from .assistant.offline import OfflineAssistant
from .assistant.service import AssistantService
from .auth.providers import AuthProvider, DisabledAuthProvider, StaticTokenAuthProvider
from .conversations.repository import ConversationRepository, InMemoryConversationRepository
from .conversations.service import ConversationService
from .core.cache import TTLCache
from .core.clock import Clock, SystemClock
from .core.config import ConfigError, Settings
from .core.events import CompositeEventPublisher, EventPublisher, InMemoryEventPublisher, LoggingEventPublisher
from .core.flags import FeatureFlags
from .core.locks import InMemoryLockStore, LockStore
from .core.metrics import Metrics
from .core.observability import Redactor
from .core.rate_limit import InMemorySlidingWindowRateLimiter, RateLimiter
from .core.resilience import CircuitBreaker
from .core.tracing import CompositeTraceSink, InMemoryTraceSink, LoggingTraceSink, TraceSink
from .knowledge.provider import JsonKnowledgeProvider, KnowledgeProvider
from .knowledge.retrieval import FullContextRetriever, KeywordRetriever
from .llm.anthropic_provider import AnthropicProvider
from .llm.glm_provider import GLMProvider
from .llm.mock_provider import LatencyMockProvider
from .llm.provider import LLMProvider
from .llm.router import ModelRouter
from .reservations.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from .reservations.provider import MockReservationProvider, ReservationProvider, ResilientReservationProvider
from .tenancy import TenantRegistry
from .tools.base import ToolRegistry
from .tools.builtin import CheckAvailabilityTool, CreateBookingTool, RequestBookingDetailsTool


@dataclass
class StateStores:
    conversations: ConversationRepository
    locks: LockStore
    rate_limiter: RateLimiter
    idempotency: IdempotencyStore
    redis: Any = None


def build_state(settings: Settings, clock: Clock, metrics: Metrics) -> StateStores:
    if settings.state_backend == "redis":
        from .state import redis_backend as rb  # imported only when selected

        client = rb.connect(settings.redis_url)  # type: ignore[arg-type]
        prefix = settings.redis_key_prefix
        return StateStores(
            conversations=rb.RedisConversationRepository(client, prefix, clock),
            locks=rb.RedisLockStore(client, prefix),
            rate_limiter=rb.RedisSlidingWindowRateLimiter(client, prefix, on_error=lambda: metrics.state_backend_errors_total.labels("rate_limiter").inc()),
            idempotency=rb.RedisIdempotencyStore(client, prefix),
            redis=client,
        )
    return StateStores(
        conversations=InMemoryConversationRepository(clock, settings.conversation_max_active),
        locks=InMemoryLockStore(),
        rate_limiter=InMemorySlidingWindowRateLimiter(),
        idempotency=InMemoryIdempotencyStore(),
    )


def build_reservation_provider(settings: Settings, idempotency: IdempotencyStore) -> ReservationProvider:
    if settings.reservation_provider == "cloudbeds":
        from .reservations.cloudbeds_provider import CloudbedsReservationProvider, parse_property_ids  # imported only when selected

        return CloudbedsReservationProvider(
            settings.cloudbeds_api_key,  # type: ignore[arg-type]
            parse_property_ids(settings.cloudbeds_property_ids),
            base_url=settings.cloudbeds_base_url,
            timeout_seconds=settings.reservation_timeout_seconds,
        )
    return MockReservationProvider(settings.data_dir, idempotency)


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "glm":
        return GLMProvider(
            settings.llm_base_url or "", settings.llm_api_key or "", timeout_seconds=settings.llm_timeout_seconds, max_retries=settings.llm_max_retries
        )
    if settings.llm_provider == "anthropic":
        return AnthropicProvider.from_settings(
            settings.anthropic_api_key or "", settings.llm_timeout_seconds, settings.llm_max_retries, settings.refusal_fallback, settings.anthropic_base_url
        )
    if settings.llm_provider == "mock":
        return LatencyMockProvider(settings.mock_llm_latency_ms)
    raise ConfigError(f"No LLM provider for LLM_PROVIDER={settings.llm_provider}")


@dataclass
class Container:
    settings: Settings
    clock: Clock
    flags: FeatureFlags
    metrics: Metrics
    events: EventPublisher
    recent_events: InMemoryEventPublisher
    traces: TraceSink
    recent_traces: InMemoryTraceSink
    tenants: TenantRegistry
    knowledge: KnowledgeProvider
    reservations: ReservationProvider
    tools: ToolRegistry
    router: ModelRouter
    llm_provider: LLMProvider | None
    assistant: AssistantService
    conversations: ConversationService
    rate_limiter: RateLimiter
    auth: AuthProvider
    redactor: Redactor
    state: StateStores
    audit: Any = None  # PostgresAuditSink when DATABASE_URL is set

    def close(self) -> None:
        """Graceful shutdown: flush audit events, close pools and clients."""
        if self.audit is not None:
            self.audit.close()
        for component in (self.llm_provider, getattr(self.reservations, "inner", None)):
            closer = getattr(component, "close", None)
            if callable(closer):
                closer()
        if self.state.redis is not None:
            self.state.redis.close()


def build_container(
    settings: Settings,
    *,
    clock: Clock | None = None,
    llm_provider: LLMProvider | None = None,
    reservation_provider: ReservationProvider | None = None,
    extra_trace_sink: TraceSink | None = None,
) -> Container:
    clock = clock or SystemClock()
    flags = FeatureFlags(settings.feature_flags)
    if flags.is_enabled("semantic_retrieval_enabled"):
        raise ConfigError("semantic_retrieval_enabled is set, but no semantic retriever is implemented in this build")

    metrics = Metrics()
    state = build_state(settings, clock, metrics)
    audit = None
    if settings.database_url:
        from .db.audit import PostgresAuditSink  # imported only when configured

        audit = PostgresAuditSink(settings.database_url, metrics)
    recent_events = InMemoryEventPublisher(maxlen=500)
    events = CompositeEventPublisher(LoggingEventPublisher(), recent_events, *([audit] if audit else []))
    recent_traces = InMemoryTraceSink(maxlen=500)
    sinks: list[TraceSink] = [LoggingTraceSink(), recent_traces] + ([extra_trace_sink] if extra_trace_sink else [])
    traces = CompositeTraceSink(*sinks)
    cache = TTLCache()

    tenants = TenantRegistry.load(settings.data_dir / "tenants.json")
    for tenant in tenants.all():
        FeatureFlags(tenant.feature_flags)  # unknown flag names fail fast
        if tenant.feature_flags.get("semantic_retrieval_enabled"):
            raise ConfigError(f"Tenant {tenant.id} enables semantic_retrieval_enabled, but no semantic retriever is implemented in this build")
    knowledge = JsonKnowledgeProvider(settings.data_dir, cache, settings.knowledge_cache_ttl_seconds)
    if audit is not None:
        audit.sync_tenants(tenants, knowledge)
    inner_reservations = reservation_provider or build_reservation_provider(settings, state.idempotency)
    reservations = ResilientReservationProvider(
        inner_reservations,
        CircuitBreaker(f"reservations:{inner_reservations.name}", settings.circuit_breaker_failures, settings.circuit_breaker_reset_seconds),
        cache,
        timeout_seconds=settings.reservation_timeout_seconds,
        read_retries=settings.reservation_read_retries,
        availability_ttl_seconds=settings.availability_cache_ttl_seconds,
    )

    tools = ToolRegistry(
        [CheckAvailabilityTool(reservations, metrics, events), RequestBookingDetailsTool(), CreateBookingTool(reservations, events)],
        flags,
        metrics,
        events,
    )
    router = ModelRouter.from_settings(settings)
    redactor = Redactor(settings.secret_values())

    if llm_provider is None and settings.llm_configured:
        llm_provider = build_llm_provider(settings)
    ai = (
        AIAssistant(
            llm_provider,
            tools,
            router,
            FullContextRetriever(),
            OutputGuardrails(redactor, flags.is_enabled("guardrail_price_check_enabled")),
            metrics,
        )
        if llm_provider is not None
        else None
    )
    offline = OfflineAssistant(tools, KeywordRetriever())
    assistant = AssistantService(
        tenants, knowledge, ai, offline, InputGuardrails(), flags, metrics, events, traces, clock, mask_contact_details=settings.pii_mask_contact_details
    )
    conversations = ConversationService(
        state.conversations,
        state.locks,
        assistant,
        knowledge,
        reservations,
        events,
        metrics,
        clock,
        ttl_seconds=settings.conversation_ttl_seconds,
        max_messages=settings.conversation_max_messages,
        context_window=settings.conversation_context_window,
        lock_lease_seconds=settings.conversation_lock_lease_seconds,
        lock_wait_seconds=settings.conversation_lock_wait_seconds,
    )
    auth: AuthProvider = StaticTokenAuthProvider(settings.admin_api_tokens) if settings.auth_mode == "static_token" else DisabledAuthProvider()

    return Container(
        settings=settings,
        clock=clock,
        flags=flags,
        metrics=metrics,
        events=events,
        recent_events=recent_events,
        traces=traces,
        recent_traces=recent_traces,
        tenants=tenants,
        knowledge=knowledge,
        reservations=reservations,
        tools=tools,
        router=router,
        llm_provider=llm_provider,
        assistant=assistant,
        conversations=conversations,
        rate_limiter=state.rate_limiter,
        auth=auth,
        redactor=redactor,
        state=state,
        audit=audit,
    )
