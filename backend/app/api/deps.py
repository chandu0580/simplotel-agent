"""Request-scoped helpers: tenant resolution, rate limits, admin authorization."""

from fastapi import Request

from ..auth.principal import Principal, Role
from ..container import Container
from ..core.errors import AppError, ErrorCode
from ..core.observability import bind_context
from ..core.rate_limit import RateLimitRule
from ..tenancy import Channel, TenantContext


def container_of(request: Request) -> Container:
    return request.app.state.container


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _hit(request: Request, rule: RateLimitRule) -> None:
    container = container_of(request)
    decision = container.rate_limiter.hit(rule)
    if not decision.allowed:
        container.metrics.rate_limited_total.labels(rule.dimension).inc()
        raise AppError(
            ErrorCode.RATE_LIMITED,
            "Too many requests. Please wait a moment and try again.",
            429,
            details=[{"dimension": rule.dimension}],
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )


def enforce_ip_limit(request: Request) -> None:
    s = container_of(request).settings
    if s.rate_limit_enabled:
        _hit(request, RateLimitRule("ip", client_ip(request, s.trust_proxy_headers), s.rate_limit_ip_per_minute))


def resolve_guest_context(request: Request, hotel_id: str, channel: Channel = Channel.WEB) -> TenantContext:
    container = container_of(request)
    # Count the caller before resolving the hotel, so probing unknown hotel ids is rate limited too.
    enforce_ip_limit(request)
    ctx = container.tenants.resolve(hotel_id, channel, request.state.request_id, request.state.trace_id)
    bind_context(tenant_id=ctx.tenant_id, hotel_id=ctx.hotel_id, channel=ctx.channel)
    return ctx


def enforce_rate_limits(request: Request, ctx: TenantContext | None, conversation_id: str | None = None) -> None:
    """Hotel and conversation limits (the IP limit is applied in resolve_guest_context)."""
    s = container_of(request).settings
    if not s.rate_limit_enabled:
        return
    if ctx is not None:
        _hit(request, RateLimitRule("hotel", ctx.hotel_id, s.rate_limit_hotel_per_minute))
    if conversation_id:
        # Keyed per hotel so ids sent to other hotels can't consume this hotel's conversation budget.
        scope = ctx.hotel_id if ctx else "-"
        _hit(request, RateLimitRule("conversation", f"{scope}:{conversation_id}", s.rate_limit_conversation_per_minute))


def require_admin(request: Request, tenant_id: str, role: Role, hotel_id: str | None = None) -> Principal:
    container = container_of(request)
    enforce_ip_limit(request)
    if not container.auth.configured:
        raise AppError(ErrorCode.AUTH_NOT_CONFIGURED, "Admin authentication is not configured for this deployment.", 401)
    principal = container.auth.authenticate(request.headers.get("Authorization"))
    if principal is None:
        raise AppError(ErrorCode.AUTHENTICATION_REQUIRED, "Valid credentials are required.", 401, headers={"WWW-Authenticate": "Bearer"})
    allowed = principal.can_access_hotel(tenant_id, hotel_id) if hotel_id else principal.can_access_tenant(tenant_id)
    if not allowed or not principal.has_role(role):
        raise AppError(ErrorCode.FORBIDDEN, "You don't have access to this resource.", 403)
    if hotel_id:
        container.tenants.require_hotel_in_tenant(tenant_id, hotel_id)
    bind_context(tenant_id=tenant_id, hotel_id=hotel_id)
    return principal
