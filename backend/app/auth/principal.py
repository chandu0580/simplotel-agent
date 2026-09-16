"""Identity and role model shared by the API layer and the tool framework."""

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    GUEST = "guest"  # an authenticated guest (e.g. signed in to manage a booking)
    HOTEL_STAFF = "hotel_staff"
    HOTEL_ADMIN = "hotel_admin"
    TENANT_ADMIN = "tenant_admin"
    PLATFORM_ADMIN = "platform_admin"


# Each role includes the permissions of the roles after it in this list.
_IMPLIES: dict[Role, set[Role]] = {
    Role.PLATFORM_ADMIN: {Role.TENANT_ADMIN, Role.HOTEL_ADMIN, Role.HOTEL_STAFF},
    Role.TENANT_ADMIN: {Role.HOTEL_ADMIN, Role.HOTEL_STAFF},
    Role.HOTEL_ADMIN: {Role.HOTEL_STAFF},
    Role.HOTEL_STAFF: set(),
    Role.GUEST: set(),
}

ALL_TENANTS = "*"


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str  # ALL_TENANTS only for platform admins
    roles: frozenset[Role]
    hotel_ids: frozenset[str] | None = None  # None = every hotel in the tenant

    def has_role(self, role: Role) -> bool:
        return any(role == r or role in _IMPLIES[r] for r in self.roles)

    def can_access_tenant(self, tenant_id: str) -> bool:
        if self.tenant_id == ALL_TENANTS:
            return Role.PLATFORM_ADMIN in self.roles
        return self.tenant_id == tenant_id

    def can_access_hotel(self, tenant_id: str, hotel_id: str) -> bool:
        return self.can_access_tenant(tenant_id) and (self.hotel_ids is None or hotel_id in self.hotel_ids)
