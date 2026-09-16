"""Tool framework.

Every tool call, whether proposed by the model or invoked by system code, goes through
`ToolRegistry.execute`:

    lookup → exposure check → feature flag → argument normalisation + validation
      → authorization (policy, roles, guest confirmation, idempotency key)
      → execution with timeout → audit log + metrics + events → ToolResult

The model can only *request* tools the registry exposes to it; it never executes code.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
import logging
import time
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from ..auth.principal import Principal, Role
from ..core.events import DomainEvent, EventPublisher
from ..core.flags import FeatureFlags
from ..core.metrics import Metrics
from ..core.observability import log_event
from ..core.resilience import TOOL_EXECUTOR, IntegrationTimeout, call_with_timeout
from ..core.tracing import ToolCallRecord
from ..knowledge.models import KnowledgeBase
from ..schemas import BookingContext
from ..tenancy import TenantContext

logger = logging.getLogger("hotel_assistant.tools")


class ToolPolicy(StrEnum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"


class ToolErrorCode(StrEnum):
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    NOT_EXPOSED = "NOT_EXPOSED"
    FEATURE_DISABLED = "FEATURE_DISABLED"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    TIMEOUT = "TIMEOUT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    BUSINESS_RULE = "BUSINESS_RULE"
    EXECUTION_FAILED = "EXECUTION_FAILED"


class ToolExecutionError(Exception):
    """Raised by a tool for an expected failure with a stable code."""

    def __init__(self, code: ToolErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    args_model: type[BaseModel]
    input_schema: dict[str, Any]  # strict JSON schema shown to the model
    policy: ToolPolicy
    version: int = 1
    timeout_seconds: float = 10.0
    exposed_to_model: bool = True
    required_flag: str | None = None
    required_roles: frozenset[Role] = frozenset()
    requires_confirmation: bool = False


@dataclass
class ToolContext:
    tenant: TenantContext
    kb: KnowledgeBase
    today: date
    booking_context: BookingContext | None = None
    principal: Principal | None = None
    guest_confirmed: bool = False
    idempotency_key: str | None = None
    tenant_flags: dict[str, bool] = field(default_factory=dict)


@dataclass
class ToolResult:
    tool: str
    ok: bool
    data: Any = None
    error_code: ToolErrorCode | None = None
    error_message: str | None = None
    latency_ms: int = 0
    traced_arguments: dict | None = None

    def record(self, invoked_by: str) -> ToolCallRecord:
        return ToolCallRecord(self.tool, "ok" if self.ok else "error", self.latency_ms, self.error_code, invoked_by, self.traced_arguments)


class Tool(Protocol):
    definition: ToolDefinition

    def run(self, ctx: ToolContext, args: BaseModel) -> Any: ...


def _normalise_nulls(value: Any) -> Any:
    # Some models send the string "null" for nullable fields.
    if isinstance(value, dict):
        return {k: (None if isinstance(v, str) and v.strip().lower() in {"", "null", "none"} else v) for k, v in value.items()}
    return value


class ToolRegistry:
    def __init__(self, tools: list[Tool], flags: FeatureFlags, metrics: Metrics, events: EventPublisher, clock: Callable[[], float] = time.perf_counter):
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.definition.name in self._tools:
                raise ValueError(f"Duplicate tool {tool.definition.name}")
            self._tools[tool.definition.name] = tool
        self.flags = flags
        self.metrics = metrics
        self.events = events
        self._clock = clock

    def definitions(self) -> list[ToolDefinition]:
        return [t.definition for t in self._tools.values()]

    def model_tools(self, tenant_flags: dict[str, bool] | None = None) -> list[ToolDefinition]:
        """Tools the model may request for this tenant: exposed and feature-enabled."""
        return [
            d
            for d in self.definitions()
            if d.exposed_to_model and (d.required_flag is None or self.flags.is_enabled(d.required_flag, tenant_flags))
        ]

    def execute(self, name: str, raw_args: Any, ctx: ToolContext, *, invoked_by: str = "model") -> ToolResult:
        started = self._clock()
        result = self._execute(name, raw_args, ctx, invoked_by)
        result.latency_ms = int((self._clock() - started) * 1000)
        self._observe(result, ctx, invoked_by)
        return result

    def _execute(self, name: str, raw_args: Any, ctx: ToolContext, invoked_by: str) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name, False, error_code=ToolErrorCode.UNKNOWN_TOOL, error_message=f"Unknown tool {name!r}")
        d = tool.definition
        if invoked_by == "model" and not d.exposed_to_model:
            return ToolResult(name, False, error_code=ToolErrorCode.NOT_EXPOSED, error_message="Tool is not available to the assistant")
        if d.required_flag and not self.flags.is_enabled(d.required_flag, ctx.tenant_flags):
            return ToolResult(name, False, error_code=ToolErrorCode.FEATURE_DISABLED, error_message=f"{d.required_flag} is off")
        try:
            args = d.args_model.model_validate(_normalise_nulls(raw_args))
        except ValidationError as exc:
            return ToolResult(name, False, error_code=ToolErrorCode.INVALID_ARGUMENTS, error_message=f"{exc.error_count()} validation errors")

        traced = args.model_dump(mode="json", exclude_none=True) if d.policy == ToolPolicy.READ_ONLY else None
        denial = self._authorize(d, ctx)
        if denial is not None:
            return ToolResult(name, False, error_code=denial, error_message="Not authorized to run this tool", traced_arguments=traced)

        try:
            data = call_with_timeout(lambda: tool.run(ctx, args), d.timeout_seconds, TOOL_EXECUTOR)
        except IntegrationTimeout:
            return ToolResult(name, False, error_code=ToolErrorCode.TIMEOUT, error_message="Tool timed out", traced_arguments=traced)
        except ToolExecutionError as exc:
            return ToolResult(name, False, error_code=exc.code, error_message=exc.message, traced_arguments=traced)
        except Exception:
            logger.exception("tool_crashed tool=%s", name)
            return ToolResult(name, False, error_code=ToolErrorCode.EXECUTION_FAILED, error_message="Tool failed", traced_arguments=traced)
        return ToolResult(name, True, data=data, traced_arguments=traced)

    @staticmethod
    def _authorize(d: ToolDefinition, ctx: ToolContext) -> ToolErrorCode | None:
        if d.required_roles:
            if ctx.principal is None:
                return ToolErrorCode.AUTHENTICATION_REQUIRED
            if not ctx.principal.can_access_hotel(ctx.tenant.tenant_id, ctx.tenant.hotel_id):
                return ToolErrorCode.FORBIDDEN
            if not any(ctx.principal.has_role(r) for r in d.required_roles):
                return ToolErrorCode.FORBIDDEN
        if d.policy == ToolPolicy.MUTATING:
            if d.requires_confirmation and not ctx.guest_confirmed:
                return ToolErrorCode.CONFIRMATION_REQUIRED
            if not ctx.idempotency_key:
                return ToolErrorCode.IDEMPOTENCY_KEY_REQUIRED
        return None

    def _observe(self, result: ToolResult, ctx: ToolContext, invoked_by: str) -> None:
        tool = self._tools.get(result.tool)
        policy = tool.definition.policy.value if tool else "unknown"
        status = "ok" if result.ok else "error"
        self.metrics.tool_calls_total.labels(result.tool if tool else "unknown", status).inc()
        self.metrics.tool_latency_ms.labels(result.tool if tool else "unknown").observe(result.latency_ms)
        # Audit trail: who asked for which tool, under which policy, and what happened. Arguments are
        # not logged, because mutating tools can carry guest references.
        log_event(
            logger,
            "tool_audit",
            tool=result.tool,
            policy=policy,
            invoked_by=invoked_by,
            principal=ctx.principal.subject if ctx.principal else None,
            status=status,
            error_code=result.error_code,
            latency_ms=result.latency_ms,
        )
        if not result.ok:
            self.metrics.tool_failures_total.labels(result.tool if tool else "unknown", str(result.error_code)).inc()
            self.events.publish(
                DomainEvent(
                    name="ToolFailed",
                    tenant_id=ctx.tenant.tenant_id,
                    hotel_id=ctx.tenant.hotel_id,
                    conversation_id=ctx.tenant.conversation_id,
                    channel=ctx.tenant.channel,
                    data={"tool": result.tool, "error_code": result.error_code, "invoked_by": invoked_by},
                )
            )
