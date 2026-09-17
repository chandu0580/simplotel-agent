"""Cloudbeds adapter: mapping, guest-input validation, failure classification.

Responses follow the documented shape of GET /getAvailableRoomTypes (Cloudbeds API v1.3).
No live calls: every test drives an httpx MockTransport.
"""

from datetime import date

import httpx
import pytest

from app.reservations.availability import AvailabilityValidationError
from app.reservations.cloudbeds_provider import CloudbedsReservationProvider, parse_property_ids
from app.reservations.models import AvailabilityQuery, ReservationError, ReservationErrorCode
from tests.conftest import GOA, TODAY, make_container

PROPERTY_ID = "123456"
WED, FRI = date(2026, 10, 7), date(2026, 10, 9)


def payload(rooms, property_id=PROPERTY_ID, currency="INR"):
    return {
        "success": True,
        "data": [{"propertyID": property_id, "propertyCurrency": [{"currencyCode": currency, "currencySymbol": "₹"}], "propertyRooms": rooms}],
        "count": len(rooms),
        "total": len(rooms),
    }


DELUXE = {
    "roomTypeID": "deluxe-pool-view",
    "roomTypeName": "Deluxe Pool View Room",
    "roomTypeDescription": "<p>Pool facing room</p>",
    "maxGuests": 3,
    "roomRate": 8100.0,
    "roomsAvailable": 4,
    "roomTypeFeatures": ["Air conditioning"],
    "roomRateDetailed": [{"date": "2026-10-07", "rate": 8100.0}, {"date": "2026-10-08", "rate": 8300.0}],
}
UNKNOWN_TYPE = {"roomTypeID": "cb-penthouse", "roomTypeName": "Penthouse", "maxGuests": 4, "roomRate": 30000.0, "roomsAvailable": 1}
SOLD_OUT = {"roomTypeID": "ocean-villa", "roomTypeName": "Ocean Villa", "maxGuests": 4, "roomRate": 21000.0, "roomsAvailable": 0}
TOO_SMALL = {"roomTypeID": "garden-standard", "roomTypeName": "Garden Standard Room", "maxGuests": 2, "roomRate": 5200.0, "roomsAvailable": 9}


def provider_for(handler):
    return CloudbedsReservationProvider("cbat_test_key", {GOA: PROPERTY_ID}, base_url="https://api.cloudbeds.example/api/v1.3", client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def hotel():
    container = make_container()
    return container.tenants.resolve(GOA), container.knowledge.snapshot(GOA, TODAY)


def search(provider, hotel, adults=2, children=0, check_in=WED, check_out=FRI):
    """Mirrors the tool path: the query is built with model_construct, so the provider validates it."""
    ctx, kb = hotel
    query = AvailabilityQuery.model_construct(check_in=check_in, check_out=check_out, adults=adults, children=children)
    return provider.check_availability(ctx, kb, query, TODAY)


def test_property_id_mapping_is_parsed_and_required():
    assert parse_property_ids("hotel-goa-001=123456, hotel-blr-001=234567") == {"hotel-goa-001": "123456", "hotel-blr-001": "234567"}
    assert parse_property_ids("") == {}
    with pytest.raises(ValueError):
        parse_property_ids("hotel-goa-001")


def test_request_sends_the_documented_parameters_and_the_api_key(hotel):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json=payload([DELUXE]))

    search(provider_for(handler), hotel, adults=2, children=1)

    request = sent[0]
    assert request.url.path.endswith("/getAvailableRoomTypes") and request.method == "GET"
    assert dict(request.url.params) == {
        "startDate": "2026-10-07", "endDate": "2026-10-09", "rooms": "1", "adults": "2", "children": "1",
        "propertyIDs": PROPERTY_ID, "detailedRates": "true",
    }
    assert request.headers["x-api-key"] == "cbat_test_key"


def test_live_rates_and_counts_are_mapped_and_enriched_from_the_knowledge_base(hotel):
    result = search(provider_for(lambda _r: httpx.Response(200, json=payload([DELUXE]))), hotel)

    assert result.available and result.nights == 2
    offer = result.rooms[0]
    assert offer.room_id == "deluxe-pool-view" and offer.name == "Deluxe Pool View Room"
    assert offer.total_price == 16400 and offer.nightly_rate == 8200  # summed from roomRateDetailed, not the base rate
    assert offer.rooms_left == 4 and offer.max_occupancy == 3 and offer.currency == "INR"
    assert offer.description == "Pool facing room"  # HTML stripped
    assert offer.beds == "1 king bed or 2 twin beds" and offer.size_sqm == 34 and offer.breakfast_included is True  # hotel content from the KB
    assert "2 nights from Wed 07 Oct 2026 to Fri 09 Oct 2026" in result.message


def test_room_types_unknown_to_the_knowledge_base_carry_no_invented_details(hotel):
    result = search(provider_for(lambda _r: httpx.Response(200, json=payload([UNKNOWN_TYPE]))), hotel)

    offer = result.rooms[0]
    assert offer.name == "Penthouse" and offer.rooms_left == 1
    assert offer.beds == "" and offer.size_sqm == 0 and offer.breakfast_included is None  # unknown, not "no breakfast"
    assert offer.total_price == 60000  # roomRate x 2 nights when no detailed rates are returned


def test_sold_out_and_too_small_room_types_are_excluded(hotel):
    result = search(provider_for(lambda _r: httpx.Response(200, json=payload([DELUXE, SOLD_OUT, TOO_SMALL]))), hotel, adults=3)

    assert [o.name for o in result.rooms] == ["Deluxe Pool View Room"]
    assert result.sold_out_room_names == ["Ocean Villa"]  # fits the party but has no rooms left


def test_no_availability_is_reported_honestly_with_contact_details(hotel):
    result = search(provider_for(lambda _r: httpx.Response(200, json=payload([SOLD_OUT]))), hotel)

    assert result.available is False and result.rooms == []
    assert "sold out" in result.message and "+91 832 555 0142" in result.message


def test_guest_input_is_validated_before_any_call(hotel):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload([DELUXE]))

    provider = provider_for(handler)
    for check_in, check_out, adults in [(date(2020, 1, 1), FRI, 2), (FRI, WED, 2), (WED, FRI, 0), (WED, date(2027, 1, 1), 2)]:
        with pytest.raises(AvailabilityValidationError):
            search(provider, hotel, adults=adults, check_in=check_in, check_out=check_out)
    assert calls == []  # never sends an impossible search to the PMS


def test_unmapped_hotel_is_a_business_error(hotel):
    ctx, kb = hotel
    provider = CloudbedsReservationProvider("k", {}, base_url="https://api.cloudbeds.example/api/v1.3", client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=payload([])))))
    with pytest.raises(ReservationError) as exc:
        provider.check_availability(ctx, kb, AvailabilityQuery(check_in=WED, check_out=FRI, adults=2), TODAY)
    assert exc.value.code == ReservationErrorCode.INVALID_REQUEST


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_http_failures_are_retryable_connection_errors(hotel, status):
    with pytest.raises(ConnectionError):
        search(provider_for(lambda _r: httpx.Response(status, json={"success": False})), hotel)


def test_timeouts_and_transport_errors_are_retryable(hotel):
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    for handler in (timeout, refused):
        with pytest.raises(ConnectionError):
            search(provider_for(handler), hotel)


@pytest.mark.parametrize("status", [401, 403])
def test_credential_failures_are_unavailable_and_never_leak_the_key(hotel, status):
    import io
    import logging

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("hotel_assistant.reservations")
    logger.addHandler(handler)
    try:
        with pytest.raises(ReservationError) as exc:
            search(provider_for(lambda _r: httpx.Response(status, json={"success": False, "message": "invalid key cbat_test_key"})), hotel)
    finally:
        logger.removeHandler(handler)
    assert exc.value.code == ReservationErrorCode.UNAVAILABLE
    assert "cbat_test_key" not in stream.getvalue() and "cbat_test_key" not in exc.value.message


def test_api_level_failure_and_unreadable_body_are_unavailable(hotel):
    for response in (httpx.Response(200, json={"success": False, "message": "Property not found"}), httpx.Response(200, content=b"<html>gateway</html>")):
        with pytest.raises(ReservationError) as exc:
            search(provider_for(lambda _r, r=response: r), hotel)
        assert exc.value.code == ReservationErrorCode.UNAVAILABLE
        assert "Property not found" not in exc.value.message  # guest-facing text stays generic


def test_bookings_are_refused_rather_than_inventing_guest_details(hotel):
    from app.reservations.models import BookingRequest

    ctx, kb = hotel
    provider = provider_for(lambda _r: httpx.Response(200, json=payload([DELUXE])))
    request = BookingRequest(room_id="deluxe-pool-view", check_in=WED, check_out=FRI, adults=2, guest_reference="guest-1")
    with pytest.raises(ReservationError) as exc:
        provider.create_booking(ctx, kb, request, "idem-key-0001", TODAY)
    assert exc.value.code == ReservationErrorCode.NOT_SUPPORTED
