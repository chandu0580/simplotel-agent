"""Personal-data minimisation and deletion."""

from fastapi.testclient import TestClient
import pytest

from app.container import build_container
from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.privacy import CARD, EMAIL, PHONE, minimise
from app.llm.scripted_provider import ScriptedLLMProvider
from app.main import create_app
from tests.conftest import GOA, TODAY, answer_response, make_container

BASE = f"/api/v1/hotels/{GOA}"
CARD_NUMBER = "4111 1111 1111 1111"  # standard test PAN (passes Luhn)


@pytest.mark.parametrize(
    ("text", "kinds", "forbidden"),
    [
        (f"My card is {CARD_NUMBER} exp 12/29", ["card"], "4111"),
        ("card 4111-1111-1111-1111 please", ["card"], "4111"),
        ("email me at guest.name+trip@example.co.in", ["email"], "example.co.in"),
        ("call me on +91 98765 43210 after 6", ["phone"], "98765"),
        ("my number is (022) 2345-6789", ["phone"], "2345"),
    ],
)
def test_personal_data_is_masked(text, kinds, forbidden):
    result = minimise(text)
    assert sorted(set(result.masked)) == kinds and forbidden not in result.text
    assert any(token in result.text for token in (CARD, EMAIL, PHONE))


@pytest.mark.parametrize(
    "text",
    [
        "Rooms from 2026-10-07 to 2026-10-09 for 2 adults",
        "Is the INR 12,500 rate per night?",
        "Booking reference 1234567812345678 is not a card",  # 16 digits that fail the Luhn check
        "Check-in at 14:00, 3 adults and 2 children, room 204",
        "We arrive 07/10/2026",
    ],
)
def test_ordinary_hotel_questions_are_untouched(text):
    result = minimise(text)
    assert result.text == text and result.masked == []


def test_contact_masking_can_be_disabled_but_cards_cannot():
    result = minimise(f"{CARD_NUMBER} guest@example.com", mask_contact_details=False)
    assert CARD in result.text and "guest@example.com" in result.text


def test_masking_is_idempotent():
    once = minimise(f"card {CARD_NUMBER}, mail a@b.io, phone +44 20 7946 0958").text
    assert minimise(once).text == once


class RecordingProvider(ScriptedLLMProvider):
    name = "recording"


def test_model_storage_and_trace_never_see_the_raw_values(fake_messages):
    container = make_container(fake_messages)
    fake_messages.responses.append(answer_response({"type": "answer", "text": "Check-in is from 2:00 PM.", "source_ids": ["timings.check_in_out"], "suggestions": []}))
    message = f"What time is check-in? My card is {CARD_NUMBER} and my email is guest@example.com"
    with TestClient(create_app(container=container)) as client:
        cid = client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
        client.post(f"{BASE}/conversations/{cid}/messages", json={"message": message})
        stored = client.get(f"{BASE}/conversations/{cid}").json()
    sent_to_model = str(fake_messages.calls[0]["messages"])
    assert "4111" not in sent_to_model and "guest@example.com" not in sent_to_model and CARD in sent_to_model
    assert "4111" not in str(stored) and "guest@example.com" not in str(stored)
    trace = container.recent_traces.traces[-1]
    assert trace.pii_masked == ["card", "email"]
    assert 'pii_masked_total{kind="card"} 1.0' in container.metrics.render().decode()


def test_legacy_history_is_minimised_too():
    provider = ScriptedLLMProvider([])
    container = build_container(Settings.for_tests(), clock=FixedClock(TODAY), llm_provider=provider)
    with TestClient(create_app(container=container)) as client:
        client.post("/api/chat", json={"message": "hi", "history": [{"role": "user", "content": f"card {CARD_NUMBER}"}]})
    assert all("4111" not in str(r.messages) for r in provider.requests)


def test_the_hotels_own_contacts_survive_in_the_history_the_model_sees(fake_messages):
    """Masking assistant turns put "[phone number removed]" in front of the model, which copied it.

    Guest turns are still masked; assistant turns are generated after masking, so they carry no guest
    data - only the hotel's own published phone and email, which the next reply may legitimately repeat.
    """
    container = make_container(fake_messages)
    fake_messages.responses.append(answer_response({"type": "fallback", "text": "Please call +91 832 555 0142.", "source_ids": [], "suggestions": []}))
    fake_messages.responses.append(answer_response({"type": "answer", "text": "Check-in is from 2:00 PM.", "source_ids": ["timings.check_in_out"], "suggestions": []}))

    with TestClient(create_app(container=container)) as client:
        cid = client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
        client.post(f"{BASE}/conversations/{cid}/messages", json={"message": f"my card is {CARD_NUMBER}, is there a helipad?"})
        client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "what time is check-in?"})

    history = str(fake_messages.calls[1]["messages"])
    assert "+91 832 555 0142" in history, "the hotel's own number must reach the model intact"
    assert "phone number removed" not in history
    assert "4111" not in history  # the guest's card is still masked


def test_deleted_conversation_is_gone_and_cannot_be_written_again():
    container = make_container()
    with TestClient(create_app(container=container)) as client:
        cid = client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
        client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "Is there a pool?"})
        assert client.delete(f"{BASE}/conversations/{cid}").status_code == 204
        assert client.get(f"{BASE}/conversations/{cid}").status_code == 404
        assert client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "hello again"}).status_code == 404
        assert client.delete(f"{BASE}/conversations/{cid}").status_code == 404
    assert "ConversationDeleted" in container.recent_events.names()
