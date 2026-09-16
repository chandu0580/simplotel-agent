"""Authentication providers for the admin API.

Production: put the API behind an OIDC-aware gateway, or implement `AuthProvider` by validating
JWTs (issuer, audience, expiry, signature via the IdP's JWKS) and mapping claims to `Principal`.

`DisabledAuthProvider` (the default) authenticates nobody, so admin endpoints answer 401
AUTH_NOT_CONFIGURED instead of pretending to be protected. `StaticTokenAuthProvider` exists only
for local development and is rejected by configuration validation in production.
"""

import hmac
from typing import Protocol

from .principal import ALL_TENANTS, Principal, Role


class AuthProvider(Protocol):
    configured: bool

    def authenticate(self, authorization_header: str | None) -> Principal | None: ...


class DisabledAuthProvider:
    configured = False

    def authenticate(self, authorization_header: str | None) -> Principal | None:
        return None


class StaticTokenAuthProvider:
    """ADMIN_API_TOKENS="<token>=<tenant_id|*>:<role>[|<role>][:<hotel_id>[|<hotel_id>]],..." (development only)."""

    configured = True

    def __init__(self, spec: str):
        self._principals: list[tuple[str, Principal]] = []
        for index, item in enumerate(p for p in spec.split(",") if p.strip()):
            token, _, rest = item.strip().partition("=")
            parts = rest.split(":")
            if len(token) < 16 or len(parts) < 2:
                raise ValueError(f"ADMIN_API_TOKENS entry {index} is malformed or the token is shorter than 16 characters")
            tenant_id, roles = parts[0], frozenset(Role(r) for r in parts[1].split("|"))
            hotels = frozenset(parts[2].split("|")) if len(parts) > 2 and parts[2] else None
            if tenant_id == ALL_TENANTS and Role.PLATFORM_ADMIN not in roles:
                raise ValueError("Only platform_admin tokens may use tenant '*'")
            self._principals.append((token, Principal(f"static-token-{index}", tenant_id, roles, hotels)))

    def authenticate(self, authorization_header: str | None) -> Principal | None:
        if not authorization_header or not authorization_header.lower().startswith("bearer "):
            return None
        presented = authorization_header[7:].strip()
        for token, principal in self._principals:
            if hmac.compare_digest(token.encode(), presented.encode()):
                return principal
        return None
