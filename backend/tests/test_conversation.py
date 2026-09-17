"""Conversational intents: small talk is answered naturally, never as a knowledge fallback.

The guarantees under test:
* greetings, capability questions, thanks and goodbye get a real reply in both AI and FAQ mode;
* they cost no model call, no retrieval and no tool call;
* the availability form appears only for availability intent, never for small talk or hotel questions;
* anything with hotel substance still routes to the grounded answer.
"""

from datetime import date

from fastapi.testclient import TestClient
import pytest

from app.assistant.conversation import classify
from app.container import build_container
from app.core.clock import FixedClock
from app.core.config import Settings
from app.llm.scripted_provider import ScriptedLLMProvider
from app.main import create_app
from tests.conftest import GOA, TODAY, make_container

BASE = f"/api/v1/hotels/{GOA}"

GREETINGS = ["hi", "Hi!", "hello", "Hello there", "hey", "good morning", "Good evening!", "namaste", "hi 👋"]
CAPABILITY = ["how can you help me?", "What can you do?", "what can I ask you?", "What do you help with?",
              "what information do you have?", "Can you help me?", "Tell me what you can do.", "who are you?"]
THANKS = ["thanks", "Thank you!", "thx", "great", "perfect", "okay", "ok", "got it", "that's helpful"]
GOODBYE = ["bye", "Goodbye!", "see you", "take care", "good night"]
SMALLTALK = ["how are you?", "How are u", "what's up", "can I ask something?", "can I ask you a question?", "nice to meet you", "are you there?"]
HOTEL_QUESTIONS = ["what time is check-in?", "is breakfast included?", "do you have a pool?", "what is the cancellation policy?",
                   "Which room is suitable for 3 guests?", "hi, is breakfast included?", "thanks, what about parking?"]


@pytest.mark.parametrize(("message", "expected"), [
    *[(m, "greeting") for m in GREETINGS],
    *[(m, "capability") for m in CAPABILITY],
    *[(m, "thanks") for m in THANKS],
    *[(m, "goodbye") for m in GOODBYE],
    *[(m, "smalltalk") for m in SMALLTALK],
    *[(m, None) for m in HOTEL_QUESTIONS],
    ("do you have rooms available?", None),
    ("what is the weather in Goa?", None),
    ("", None),
])
def test_classify_only_matches_pure_small_talk(message, expected):
    assert classify(message) == expected


def conversation(client):
    return client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]


@pytest.mark.parametrize("ai_enabled", [False, True])
@pytest.mark.parametrize(("message", "must_contain"), [
    ("hi", "welcome to The Palm Grove Resort"),
    ("good morning", "welcome to The Palm Grove Resort"),
    ("how can you help me?", "rooms, amenities, breakfast"),
    ("what can you do?", "room availability"),
    ("how are you?", "All good here"),
    ("can I ask you a question?", "All good here"),
    ("thanks", "You're welcome"),
    ("bye", "Thank you for visiting"),
])
def test_small_talk_is_answered_without_the_model_or_a_knowledge_fallback(ai_enabled, message, must_contain):
    provider = ScriptedLLMProvider([])  # any model call raises: small talk must not reach it
    container = build_container(Settings.for_tests(), clock=FixedClock(TODAY), llm_provider=provider if ai_enabled else None)
    with TestClient(create_app(container=container)) as client:
        body = client.post(f"{BASE}/conversations/{conversation(client)}/messages", json={"message": message}).json()

    reply = body["reply"]
    assert reply["type"] == "clarification" and must_contain in reply["text"]
    assert len(reply["text"]) <= 170, f"conversational replies stay short: {reply['text']!r}"
    assert "couldn't find" not in reply["text"] and "don't have reliable information" not in reply["text"]
    assert reply["booking_prefill"] is None and reply["availability"] is None
    assert provider.requests == []  # no tokens spent on small talk
    trace = container.recent_traces.traces[-1]
    assert trace.mode == "conversational" and trace.fallback_used is False and trace.tool_calls == [] and trace.llm_latency_ms is None
    assert body["mode"] == ("ai" if ai_enabled else "offline")  # the badge keeps showing the real mode


def test_greeting_and_capability_offer_starter_suggestions_but_thanks_does_not():
    container = make_container()
    with TestClient(create_app(container=container)) as client:
        cid = conversation(client)
        greeting = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "hi"}).json()["reply"]
        thanks = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "thanks"}).json()["reply"]
    assert greeting["suggestions"] == ["What time is check-in?", "Is breakfast included?", "Check room availability"]
    assert thanks["suggestions"] == []


@pytest.mark.parametrize("message", [*GREETINGS[:3], *CAPABILITY[:3], *SMALLTALK[:3], *THANKS[:3], *GOODBYE[:2], *HOTEL_QUESTIONS])
def test_the_booking_form_never_opens_for_small_talk_or_hotel_questions(message):
    """The availability form belongs to the availability intent only (offline engine: deterministic routing)."""
    with TestClient(create_app(container=make_container())) as client:
        reply = client.post(f"{BASE}/conversations/{conversation(client)}/messages", json={"message": message}).json()["reply"]
    assert reply["type"] != "collect_booking_details" and reply["booking_prefill"] is None, reply["text"][:120]


@pytest.mark.parametrize("message", ["do you have rooms available?", "I want to check room availability", "any rooms free next week?"])
def test_availability_intent_still_opens_the_form(message):
    with TestClient(create_app(container=make_container())) as client:
        reply = client.post(f"{BASE}/conversations/{conversation(client)}/messages", json={"message": message}).json()["reply"]
    assert reply["type"] == "collect_booking_details" and reply["booking_prefill"] is not None


def test_small_talk_keeps_the_conversation_and_context_intact():
    """Greeting, then a hotel question, then a follow-up: context survives the conversational turns."""
    container = make_container()
    with TestClient(create_app(container=container)) as client:
        cid = conversation(client)
        client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "hi"})
        rooms = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "Which room is suitable for 3 guests?"}).json()["reply"]
        client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "thanks"})
        availability = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "is anything available from 2026-10-07 to 2026-10-09 for 3 adults?"}).json()["reply"]
        view = client.get(f"{BASE}/conversations/{cid}").json()

    assert rooms["type"] == "answer" and "Deluxe Pool View Room" in rooms["text"]
    assert availability["type"] == "availability" and availability["availability"]["adults"] == 3
    assert len(view["messages"]) == 8  # every turn, including small talk, is part of the transcript


# ---------- Availability intent with incomplete information (deterministic engine) ----------


def test_confident_relative_dates_are_resolved_and_ambiguous_ones_are_asked_about():
    """"this weekend" is resolvable; "next week" is not, and dates are never invented."""
    from app.assistant.offline import resolve_relative_range

    monday = date(2026, 10, 5)
    assert resolve_relative_range("any rooms this weekend?", monday) == [date(2026, 10, 10), date(2026, 10, 11)]
    assert resolve_relative_range("rooms next weekend please", monday) == [date(2026, 10, 17), date(2026, 10, 18)]
    assert resolve_relative_range("anything tomorrow?", monday) == [date(2026, 10, 6), date(2026, 10, 7)]
    assert resolve_relative_range("a room tonight", monday) == [monday, date(2026, 10, 6)]
    saturday = date(2026, 10, 10)
    assert resolve_relative_range("rooms this weekend", saturday) == [saturday, date(2026, 10, 11)]  # today when today is Saturday
    for ambiguous in ["any rooms next week?", "rooms soon", "do you have rooms available?", "rooms in December"]:
        assert resolve_relative_range(ambiguous, monday) == []


def test_availability_with_dates_and_party_runs_the_search_without_a_form():
    with TestClient(create_app(container=make_container())) as client:
        reply = client.post(f"{BASE}/conversations/{conversation(client)}/messages", json={"message": "Do you have rooms this weekend for 2 adults?"}).json()["reply"]
    assert reply["type"] == "availability"
    assert reply["availability"]["check_in"] == "2026-10-10" and reply["availability"]["adults"] == 2


@pytest.mark.parametrize(("message", "expected_prefill", "asks_for"), [
    ("Do you have rooms for 3 adults?", {"adults": 3, "check_in": None}, "check-in and check-out dates"),
    ("Do you have rooms available next weekend?", {"adults": None, "check_in": "2026-10-17"}, "How many guests"),
    ("do you have rooms available?", {"adults": None, "check_in": None}, "dates and the number of guests"),
])
def test_incomplete_availability_asks_only_for_what_is_missing(message, expected_prefill, asks_for):
    with TestClient(create_app(container=make_container())) as client:
        reply = client.post(f"{BASE}/conversations/{conversation(client)}/messages", json={"message": message}).json()["reply"]
    assert reply["type"] == "collect_booking_details"
    for field, value in expected_prefill.items():
        assert reply["booking_prefill"][field] == value
    assert asks_for in reply["text"]


def test_progressive_conversation_greeting_to_knowledge_to_availability():
    """The brief's target journey: small talk, then a room question, a follow-up, then availability."""
    container = make_container()
    with TestClient(create_app(container=container)) as client:
        cid = conversation(client)
        greeting = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "Hi"}).json()["reply"]
        capability = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "How can you help me?"}).json()["reply"]
        rooms = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "Which room is suitable for 3 adults?"}).json()["reply"]
        breakfast = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "Does it include breakfast?"}).json()["reply"]
        availability = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "Is it available this weekend for 3 adults?"}).json()["reply"]

    assert greeting["type"] == "clarification" and capability["type"] == "clarification"
    assert rooms["type"] == "answer" and "Deluxe Pool View Room" in rooms["text"]
    assert breakfast["type"] == "answer" and "Breakfast" in breakfast["text"]
    assert availability["type"] == "availability" and availability["availability"]["adults"] == 3
    # Small talk never produced a form or a fallback along the way.
    assert greeting["booking_prefill"] is None and capability["booking_prefill"] is None
