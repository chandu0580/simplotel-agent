"""Run inside each backend replica at the same moment (see docs/SRE.md, "Multi-replica verification").

    FEATURE_BOOKING_TOOLS_ENABLED=true python -m scripts.replica_booking_probe <idempotency-key> <start-at-epoch-seconds>

Builds a container from the replica's own environment (so it shares that replica's Redis and PostgreSQL),
waits until the agreed start time, then creates a booking with the given idempotency key and prints
`booking_id=<id> created_here=<bool>`. With a shared idempotency store every replica must print the same id,
and exactly one must report created_here=True. The mock provider holds bookings in process memory only.
"""

from datetime import date
import sys
import time

from app.auth.principal import Principal, Role
from app.container import build_container
from app.core.config import Settings
from app.tools.base import ToolContext

HOTEL = "hotel-goa-001"


def main() -> int:
    key, start_at = sys.argv[1], float(sys.argv[2])
    container = build_container(Settings.from_env())
    tenant = container.tenants.resolve(HOTEL)
    today = date(2026, 9, 16)
    ctx = ToolContext(
        tenant=tenant,
        kb=container.knowledge.snapshot(HOTEL, today),
        today=today,
        principal=Principal("guest-probe", tenant.tenant_id, frozenset({Role.GUEST}), frozenset({HOTEL})),
        guest_confirmed=True,
        idempotency_key=key,
    )
    args = {"room_id": "garden-standard", "check_in": "2026-10-07", "check_out": "2026-10-08", "adults": 2, "children": 0}
    time.sleep(max(0.0, start_at - time.time()))
    result = container.tools.execute("create_booking", args, ctx, invoked_by="api")
    created_here = bool(container.reservations.inner.bookings_for(tenant))
    if container.audit is not None:
        container.audit.flush(10)
    container.close()
    if not result.ok:
        print(f"error={result.error_code}")
        return 1
    print(f"booking_id={result.data.booking_id} created_here={created_here}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
