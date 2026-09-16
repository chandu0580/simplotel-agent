"""Tenant boundary.

A *tenant* is a customer (hotel group); each *hotel* belongs to exactly one tenant and hotel ids
are globally unique. Every request resolves a `TenantContext` before touching hotel data, and
every repository/provider call is scoped by it, so Hotel A's requests cannot read Hotel B's data.
"""

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path

from pydantic import BaseModel, Field

from .core.errors import AppError, ErrorCode


class Channel(StrEnum):
    WEB = "web"
    WHATSAPP = "whatsapp"
    VOICE = "voice"
    API = "api"


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    hotel_id: str
    channel: Channel = Channel.WEB
    conversation_id: str | None = None
    request_id: str = "-"
    trace_id: str = "-"

    def with_conversation(self, conversation_id: str) -> "TenantContext":
        return TenantContext(self.tenant_id, self.hotel_id, self.channel, conversation_id, self.request_id, self.trace_id)


class Tenant(BaseModel):
    id: str
    name: str
    status: str = "active"  # active | suspended
    hotels: list[str]
    feature_flags: dict[str, bool] = Field(default_factory=dict)


class TenantRegistry:
    def __init__(self, tenants: list[Tenant], default_hotel_id: str):
        self._tenants = {t.id: t for t in tenants}
        self._hotel_to_tenant: dict[str, str] = {}
        for tenant in tenants:
            for hotel_id in tenant.hotels:
                if hotel_id in self._hotel_to_tenant:
                    raise ValueError(f"Hotel {hotel_id} is assigned to more than one tenant")
                self._hotel_to_tenant[hotel_id] = tenant.id
        if default_hotel_id not in self._hotel_to_tenant:
            raise ValueError(f"default_hotel_id {default_hotel_id} is not assigned to any tenant")
        self.default_hotel_id = default_hotel_id

    @classmethod
    def load(cls, path: Path) -> "TenantRegistry":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls([Tenant(**t) for t in raw["tenants"]], raw["default_hotel_id"])

    def tenant(self, tenant_id: str) -> Tenant:
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise AppError(ErrorCode.NOT_FOUND, "Tenant not found.", 404)
        return tenant

    def tenant_for_hotel(self, hotel_id: str) -> Tenant:
        tenant_id = self._hotel_to_tenant.get(hotel_id)
        tenant = self._tenants.get(tenant_id) if tenant_id else None
        if tenant is None or tenant.status != "active":
            raise AppError(ErrorCode.HOTEL_NOT_FOUND, "Hotel not found.", 404)
        return tenant

    def resolve(self, hotel_id: str, channel: Channel = Channel.WEB, request_id: str = "-", trace_id: str = "-") -> TenantContext:
        tenant = self.tenant_for_hotel(hotel_id)
        return TenantContext(tenant.id, hotel_id, channel, None, request_id, trace_id)

    def all(self) -> list[Tenant]:
        return list(self._tenants.values())

    def hotel_ids(self) -> list[str]:
        return list(self._hotel_to_tenant)

    def require_hotel_in_tenant(self, tenant_id: str, hotel_id: str) -> None:
        # Deliberately the same 404 whether the hotel doesn't exist or belongs to another tenant,
        # so callers can't probe other tenants' hotel ids.
        if self._hotel_to_tenant.get(hotel_id) != tenant_id:
            raise AppError(ErrorCode.HOTEL_NOT_FOUND, "Hotel not found.", 404)
