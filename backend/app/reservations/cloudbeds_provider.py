"""Cloudbeds reservation adapter: live availability and rates from a real PMS.

Implements `ReservationProvider` against the documented Cloudbeds API (v1.3):
`GET /getAvailableRoomTypes`, authenticated with `x-api-key`
(https://developers.cloudbeds.com/reference/get_getavailableroomtypes-2).

What it does and doesn't do:

* **Availability and prices come from Cloudbeds**, never from the model or from `inventory.json`.
  Guest-input validation (past dates, check-out before check-in, stay length, party size) still runs
  locally first, so the guest gets the same messages whatever the provider is.
* Cloudbeds returns a room type's live rate, availability count and max guests, but not the bed
  configuration, room size or whether breakfast is included. Those are hotel content, so they are
  taken from the knowledge base when a room type matches by id or name, and left empty otherwise
  (the UI omits what is missing rather than showing a wrong "0 m²" or "no breakfast").
* **Booking is not implemented.** `postReservation` requires guest first name, last name, country,
  postcode, email and a payment method. This assistant deliberately collects none of that
  (see docs/PRIVACY.md), so `create_booking` raises NOT_SUPPORTED instead of inventing guest data.
  Enabling real bookings is a product decision: collect the details in the UI first.

Timeouts, retries, the circuit breaker and caching are applied by `ResilientReservationProvider`,
so transport failures are raised as ConnectionError (retryable) and business problems as
ReservationError (not retried, never trips the breaker).
"""

from datetime import date, timedelta
import logging
import re

import httpx

from ..knowledge.models import KnowledgeBase, Room
from ..schemas import AvailabilityResult, RoomOffer
from ..tenancy import TenantContext
from .availability import validate_search
from .models import AvailabilityQuery, Booking, BookingRequest, ReservationError, ReservationErrorCode

logger = logging.getLogger("hotel_assistant.reservations")
DEFAULT_BASE_URL = "https://api.cloudbeds.com/api/v1.3"
_TAGS = re.compile(r"<[^>]+>")


def parse_property_ids(raw: str) -> dict[str, str]:
    """`hotel-goa-001=123456,hotel-blr-001=234567` → {hotel_id: propertyID}."""
    mapping = {}
    for part in raw.split(","):
        if not part.strip():
            continue
        hotel_id, _, property_id = part.partition("=")
        if not hotel_id.strip() or not property_id.strip():
            raise ValueError(f"CLOUDBEDS_PROPERTY_IDS entry must be <hotel_id>=<propertyID>, got {part!r}")
        mapping[hotel_id.strip()] = property_id.strip()
    return mapping


def _clean(text: str | None) -> str:
    return _TAGS.sub(" ", text or "").replace("&nbsp;", " ").strip()


def _match_known_room(kb: KnowledgeBase, room_type_id: str, name: str) -> Room | None:
    """Bed configuration, size and breakfast are hotel content, not PMS data."""
    normalised = name.strip().casefold()
    return next((r for r in kb.rooms if r.id == room_type_id or r.name.strip().casefold() == normalised), None)


class CloudbedsReservationProvider:
    name = "cloudbeds"

    def __init__(
        self,
        api_key: str,
        property_ids: dict[str, str],
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 5.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.property_ids = property_ids
        # The key is sent per request, so an injected client (tests, custom transport) is still authenticated.
        self._headers = {"x-api-key": api_key, "Accept": "application/json"}
        self._client = client or httpx.Client(timeout=httpx.Timeout(connect=min(5.0, timeout_seconds), read=timeout_seconds, write=10.0, pool=5.0))

    # ---------- reads ----------

    def check_availability(self, ctx: TenantContext, kb: KnowledgeBase, query: AvailabilityQuery, today: date) -> AvailabilityResult:
        validate_search(query.check_in, query.check_out, query.adults, query.children, today)
        if kb.hotel.id != ctx.hotel_id:
            raise ReservationError(ReservationErrorCode.INVALID_REQUEST, "Knowledge snapshot does not belong to this hotel.")
        property_id = self.property_ids.get(ctx.hotel_id)
        if not property_id:
            raise ReservationError(ReservationErrorCode.INVALID_REQUEST, f"No Cloudbeds property is configured for hotel {ctx.hotel_id}.")

        payload = self._get(
            "/getAvailableRoomTypes",
            {
                "startDate": query.check_in.isoformat(),
                "endDate": query.check_out.isoformat(),
                "rooms": 1,
                "adults": query.adults,
                "children": query.children,
                "propertyIDs": property_id,
                "detailedRates": "true",
            },
        )
        return self._to_result(payload, property_id, kb, query, today)

    def get_room(self, ctx: TenantContext, kb: KnowledgeBase, room_id: str) -> Room | None:
        # Room descriptions are hotel content; the PMS is the source for availability and rates only.
        return kb.room(room_id) if kb.hotel.id == ctx.hotel_id else None

    def is_healthy(self) -> bool:
        return True  # the circuit breaker in ResilientReservationProvider tracks real failures

    def close(self) -> None:
        self._client.close()

    # ---------- mutations (not supported: see the module docstring) ----------

    def create_booking(self, ctx: TenantContext, kb: KnowledgeBase, request: BookingRequest, idempotency_key: str, today: date) -> Booking:
        raise ReservationError(
            ReservationErrorCode.NOT_SUPPORTED,
            "Bookings are made on the hotel's booking engine; this assistant cannot create reservations.",
        )

    def modify_booking(self, ctx: TenantContext, booking_id: str, changes: dict, idempotency_key: str) -> Booking:
        raise ReservationError(ReservationErrorCode.NOT_SUPPORTED, "Booking changes are not supported.")

    def cancel_booking(self, ctx: TenantContext, booking_id: str, idempotency_key: str) -> Booking:
        raise ReservationError(ReservationErrorCode.NOT_SUPPORTED, "Cancellations are not supported.")

    # ---------- HTTP ----------

    def _get(self, path: str, params: dict) -> dict:
        try:
            response = self._client.get(f"{self.base_url}{path}", params=params, headers=self._headers)
        except httpx.TimeoutException as exc:
            raise ConnectionError(f"Cloudbeds timed out: {type(exc).__name__}") from exc  # retried by the wrapper
        except httpx.TransportError as exc:
            raise ConnectionError(f"Cloudbeds unreachable: {type(exc).__name__}") from exc

        if response.status_code in (401, 403):
            logger.error("cloudbeds_auth_failed status=%s", response.status_code)  # never log the key or body
            raise ReservationError(ReservationErrorCode.UNAVAILABLE, "The booking system rejected our credentials.")
        if response.status_code == 429 or response.status_code >= 500:
            raise ConnectionError(f"Cloudbeds returned {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ReservationError(ReservationErrorCode.UNAVAILABLE, "The booking system returned an unreadable response.") from exc
        if response.status_code >= 400 or not payload.get("success", False):
            message = _clean(payload.get("message")) or f"HTTP {response.status_code}"
            logger.warning("cloudbeds_request_failed path=%s status=%s message=%s", path, response.status_code, message)
            raise ReservationError(ReservationErrorCode.UNAVAILABLE, "Live availability is temporarily unavailable.")
        return payload

    # ---------- mapping ----------

    def _to_result(self, payload: dict, property_id: str, kb: KnowledgeBase, query: AvailabilityQuery, today: date) -> AvailabilityResult:
        nights = (query.check_out - query.check_in).days
        party = query.adults + query.children
        properties = [p for p in payload.get("data") or [] if str(p.get("propertyID", "")) == property_id] or (payload.get("data") or [])
        rooms_payload = properties[0].get("propertyRooms") or [] if properties else []
        currency = self._currency(properties[0] if properties else {}, kb)

        offers: list[RoomOffer] = []
        sold_out: list[str] = []
        for room in rooms_payload:
            name = _clean(room.get("roomTypeName")) or _clean(room.get("roomTypeNameShort")) or str(room.get("roomTypeID", "room"))
            max_guests = int(room.get("maxGuests") or 0)
            if max_guests and max_guests < party:
                continue  # cannot hold this party
            available = int(room.get("roomsAvailable") or 0)
            if available <= 0:
                sold_out.append(name)
                continue
            total = self._total_price(room, nights)
            known = _match_known_room(kb, str(room.get("roomTypeID", "")), name)
            offers.append(
                RoomOffer(
                    room_id=str(room.get("roomTypeID", "")),
                    name=name,
                    description=_clean(room.get("roomTypeDescription")) or (known.description if known else ""),
                    beds=known.beds if known else "",
                    size_sqm=known.size_sqm if known else 0,
                    max_occupancy=max_guests or (known.max_occupancy if known else party),
                    breakfast_included=bool(known.breakfast_included) if known else None,
                    rooms_left=available,
                    nightly_rate=round(total / nights) if nights else total,
                    total_price=total,
                    currency=currency,
                    features=[_clean(f) for f in (room.get("roomTypeFeatures") or []) if _clean(f)] or (known.features if known else []),
                )
            )

        offers.sort(key=lambda o: o.total_price)
        return AvailabilityResult(
            check_in=query.check_in,
            check_out=query.check_out,
            nights=nights,
            adults=query.adults,
            children=query.children,
            available=bool(offers),
            rooms=offers,
            sold_out_room_names=sold_out,
            message=self._message(offers, sold_out, kb, query, nights),
        )

    def _currency(self, property_payload: dict, kb: KnowledgeBase) -> str:
        currencies = property_payload.get("propertyCurrency") or []
        code = currencies[0].get("currencyCode") if currencies and isinstance(currencies[0], dict) else None
        return (code or kb.hotel.currency).upper()

    def _total_price(self, room: dict, nights: int) -> int:
        detailed = room.get("roomRateDetailed") or []
        if detailed:
            return round(sum(float(d.get("rate") or 0) for d in detailed))
        return round(float(room.get("roomRate") or 0) * max(nights, 1))

    def _message(self, offers: list[RoomOffer], sold_out: list[str], kb: KnowledgeBase, query: AvailabilityQuery, nights: int) -> str:
        party = f"{query.adults} adult{'s' if query.adults != 1 else ''}"
        if query.children:
            party += f" and {query.children} child{'ren' if query.children != 1 else ''}"
        stay = f"{nights} night{'s' if nights != 1 else ''} from {query.check_in:%a %d %b %Y} to {query.check_out:%a %d %b %Y}"
        if offers:
            return f"{len(offers)} room type{'s' if len(offers) != 1 else ''} available for {party}, {stay}."
        if sold_out:
            return f"Sorry, all suitable rooms are sold out for {party}, {stay}. Try different dates or contact us: {kb.contact_line()}."
        return f"No rooms are available for {party}, {stay}. Try different dates or contact us: {kb.contact_line()}."


def date_range(check_in: date, check_out: date) -> list[date]:
    return [check_in + timedelta(days=i) for i in range((check_out - check_in).days)]
