"""Tenant isolation: one hotel's requests can never read or change another hotel's data."""

from datetime import date

import pytest

from app.core.errors import AppError
from app.reservations.models import AvailabilityQuery, ReservationError
from app.tenancy import Tenant, TenantRegistry
from tests.conftest import BLR, GOA, TODAY, answer_response, make_container, turn_request

V1 = "/api/v1/hotels"


def test_registry_resolves_hotels_to_their_tenant(offline_container):
    registry = offline_container.tenants
    assert registry.resolve(GOA).tenant_id == "tenant-demo"
    assert registry.resolve(BLR).tenant_id == "tenant-metro"
    with pytest.raises(AppError) as exc:
        registry.resolve("hotel-unknown")
    assert exc.value.code == "HOTEL_NOT_FOUND"


def test_registry_rejects_a_hotel_assigned_to_two_tenants():
    with pytest.raises(ValueError, match="more than one tenant"):
        TenantRegistry([Tenant(id="a", name="A", hotels=["h1"]), Tenant(id="b", name="B", hotels=["h1"])], "h1")


def test_require_hotel_in_tenant_hides_other_tenants_hotels(offline_container):
    with pytest.raises(AppError) as exc:
        offline_container.tenants.require_hotel_in_tenant("tenant-demo", BLR)
    assert exc.value.status == 404


def test_suspended_tenant_hotels_are_not_served(offline_container):
    offline_container.tenants.tenant("tenant-metro").status = "suspended"
    with pytest.raises(AppError):
        offline_container.tenants.resolve(BLR)


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("get", "", None),
        ("post", "/messages", {"message": "What time is check-in?"}),
        ("post", "/availability", {"check_in": "2026-10-07", "check_out": "2026-10-08", "adults": 2}),
        ("delete", "", None),
    ],
)
def test_conversation_from_one_hotel_is_invisible_to_another(offline_client, method, suffix, body):
    conversation_id = offline_client.post(f"{V1}/{GOA}/conversations", json={}).json()["conversation_id"]

    kwargs = {"json": body} if body is not None else {}
    response = getattr(offline_client, method)(f"{V1}/{BLR}/conversations/{conversation_id}{suffix}", **kwargs)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
    # ...and it still exists for its own hotel.
    assert offline_client.get(f"{V1}/{GOA}/conversations/{conversation_id}").status_code == 200


def test_each_hotel_answers_from_its_own_knowledge(offline_client):
    goa = offline_client.post(f"{V1}/{GOA}/conversations", json={}).json()["conversation_id"]
    blr = offline_client.post(f"{V1}/{BLR}/conversations", json={}).json()["conversation_id"]

    goa_reply = offline_client.post(f"{V1}/{GOA}/conversations/{goa}/messages", json={"message": "Does the hotel have a swimming pool?"}).json()["reply"]
    blr_reply = offline_client.post(f"{V1}/{BLR}/conversations/{blr}/messages", json={"message": "Does the hotel have a swimming pool?"}).json()["reply"]

    assert "lagoon swimming pool" in goa_reply["text"]
    assert "does not have a swimming pool" in blr_reply["text"]
    assert "+91 80 5550 0199" not in goa_reply["text"]


def test_availability_uses_the_requested_hotels_inventory(offline_client):
    tuesday = "2026-10-06"
    blr = offline_client.post(f"{V1}/{BLR}/availability", json={"check_in": tuesday, "check_out": "2026-10-07", "adults": 2}).json()
    goa = offline_client.post(f"{V1}/{GOA}/availability", json={"check_in": tuesday, "check_out": "2026-10-07", "adults": 2}).json()

    assert {r["room_id"] for r in blr["rooms"]} == {"studio-suite"}  # executive rooms sell out on Tuesdays
    assert blr["sold_out_room_names"] == ["Executive King Room"]
    assert "garden-standard" in {r["room_id"] for r in goa["rooms"]}


def test_reservation_provider_rejects_a_snapshot_from_another_hotel(offline_container):
    goa_kb = offline_container.knowledge.snapshot(GOA, TODAY)
    blr_ctx = offline_container.tenants.resolve(BLR)
    with pytest.raises(ReservationError):
        offline_container.reservations.check_availability(blr_ctx, goa_kb, AvailabilityQuery(check_in=date(2026, 10, 7), check_out=date(2026, 10, 8), adults=2), TODAY)


def test_tenant_feature_flag_disables_ai_for_that_tenant_only(fake_messages):
    container = make_container(fake_messages)
    container.tenants.tenant("tenant-metro").feature_flags["ai_assistant_enabled"] = False
    fake_messages.responses.append(answer_response({"type": "answer", "text": "Check-in is from 2:00 PM.", "source_ids": ["timings.check_in_out"], "suggestions": []}))

    goa = container.assistant.handle(turn_request(container, "What time is check-in?", hotel_id=GOA))
    blr = container.assistant.handle(turn_request(container, "What time is check-in?", hotel_id=BLR))

    assert goa.mode == "ai"
    assert blr.mode == "offline" and "3:00 PM" in blr.reply.text
    assert len(fake_messages.calls) == 1
