"""Read-only smoke check against a real Cloudbeds account.

    python -m scripts.verify_cloudbeds [--hotel hotel-goa-001] [--nights 2] [--adults 2] [--in 2026-11-10]

Reads RESERVATION_PROVIDER, CLOUDBEDS_API_KEY, CLOUDBEDS_PROPERTY_IDS and CLOUDBEDS_BASE_URL from the
environment or backend/.env. Performs one `getAvailableRoomTypes` call through the real adapter and prints
the mapped result. It never writes anything: no reservation is created, and `postReservation` is not called.

The API key is never printed; failures are reported by category only.
"""

import argparse
from datetime import date, timedelta

from app.container import build_reservation_provider
from app.core.config import Settings
from app.reservations.cloudbeds_provider import parse_property_ids
from app.reservations.models import AvailabilityQuery, ReservationError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hotel")
    parser.add_argument("--in", dest="check_in", help="YYYY-MM-DD (default: 30 days from today)")
    parser.add_argument("--nights", type=int, default=2)
    parser.add_argument("--adults", type=int, default=2)
    parser.add_argument("--children", type=int, default=0)
    args = parser.parse_args()

    settings = Settings.from_env()
    if settings.reservation_provider != "cloudbeds":
        print(f"RESERVATION_PROVIDER is {settings.reservation_provider!r}; set it to 'cloudbeds' in backend/.env first.")
        return 2

    mapping = parse_property_ids(settings.cloudbeds_property_ids)
    hotel_id = args.hotel or next(iter(mapping), "")
    if hotel_id not in mapping:
        print(f"No Cloudbeds property configured for {hotel_id!r}. CLOUDBEDS_PROPERTY_IDS maps: {sorted(mapping)}")
        return 2

    from app.container import build_container  # heavier import: only needed for tenant/knowledge context

    container = build_container(Settings.for_tests(data_dir=settings.data_dir))
    ctx = container.tenants.resolve(hotel_id)
    today = date.today()
    check_in = date.fromisoformat(args.check_in) if args.check_in else today + timedelta(days=30)
    kb = container.knowledge.snapshot(hotel_id, today)
    query = AvailabilityQuery(check_in=check_in, check_out=check_in + timedelta(days=args.nights), adults=args.adults, children=args.children)

    provider = build_reservation_provider(settings, container.state.idempotency)
    print(f"provider={provider.name} base_url={settings.cloudbeds_base_url} hotel={hotel_id} property={mapping[hotel_id]}")
    print(f"searching {query.check_in} → {query.check_out} for {query.adults} adults, {query.children} children …")
    try:
        result = provider.check_availability(ctx, kb, query, today)
    except ReservationError as exc:
        print(f"FAILED: {exc.code.value}: {exc.message}")
        return 1
    except ConnectionError as exc:
        print(f"FAILED: transport/dependency error ({exc})")
        return 1
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()

    print(f"\n{result.message}")
    for offer in result.rooms:
        known = "from hotel content" if offer.beds else "PMS only"
        print(f"  - {offer.name}: {offer.currency} {offer.total_price} total ({offer.currency} {offer.nightly_rate}/night), {offer.rooms_left} left, sleeps {offer.max_occupancy} [{known}]")
    if result.sold_out_room_names:
        print(f"  sold out: {', '.join(result.sold_out_room_names)}")
    print("\nRead-only check complete; nothing was booked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
