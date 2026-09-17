"""Conversational intents: small talk is answered naturally, never as a knowledge fallback.

The guarantees under test:
* greetings, capability questions, thanks and goodbye get a real reply in both AI and FAQ mode;
* they cost no model call, no retrieval and no tool call;
* the availability form appears only for availability intent, never for small talk or hotel questions;
* anything with hotel substance still routes to the grounded answer.
"""

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
HOTEL_QUESTIONS = ["what time is check-in?", "is breakfast included?", "do you have a pool?", "what is the cancellation policy?",
                   "Which room is suitable for 3 guests?", "hi, is breakfast included?", "thanks, what about parking?"]


@pytest.mark.parametrize(("message", "expected"), [
    *[(m, "greeting") for m in GREETINGS],
    *[(m, "capability") for m in CAPABILITY],
    *[(m, "thanks") for m in THANKS],
    *[(m, "goodbye") for m in GOODBYE],
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
    ("hi", "Welcome to The Palm Grove Resort"),
    ("good morning", "Welcome to The Palm Grove Resort"),
    ("how can you help me?", "Room availability"),
    ("what can you do?", "Breakfast and dining"),
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


@pytest.mark.parametrize("message", [*GREETINGS[:3], *CAPABILITY[:3], *THANKS[:3], *GOODBYE[:2], *HOTEL_QUESTIONS])
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
