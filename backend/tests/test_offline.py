import pytest

from app.offline import OfflineAssistant, extract_party_size, is_availability_request
from app.schemas import BookingContext, ChatRequest
from tests.conftest import TODAY


@pytest.fixture
def offline(kb):
    return OfflineAssistant(kb)


def ask(offline, message, **kwargs):
    return offline.reply(ChatRequest(message=message, **kwargs), TODAY)


@pytest.mark.parametrize(
    ("question", "source_id"),
    [
        ("What time is check-in?", "timings.check_in_out"),
        ("Does the hotel have a swimming pool?", "amenities.pool"),
        ("Is breakfast included?", "amenities.dining"),
        ("What is the cancellation policy?", "policies.cancellation"),
        ("Can I bring my dog?", "policies.pets"),
    ],
)
def test_faq_questions_answered_from_knowledge_base(offline, question, source_id):
    reply = ask(offline, question)

    assert reply.type == "answer"
    assert source_id in [s.id for s in reply.sources]


def test_room_for_three_guests_lists_only_rooms_that_fit(offline):
    reply = ask(offline, "Which room is suitable for three guests?")

    assert reply.type == "answer"
    assert {s.id for s in reply.sources} == {"rooms.deluxe-pool-view", "rooms.family-suite", "rooms.ocean-villa"}
    assert "Garden Standard" not in reply.text


def test_unknown_topic_returns_fallback_with_contact(offline, kb):
    reply = ask(offline, "Is there a helipad on the roof?")

    assert reply.type == "fallback"
    assert kb.hotel.phone in reply.text


def test_availability_without_details_asks_for_them(offline):
    reply = ask(offline, "Do you have rooms available next weekend?")

    assert reply.type == "collect_booking_details"
    assert reply.booking_prefill.check_in is None


def test_availability_with_full_details_runs_search(offline):
    reply = ask(offline, "Is anything available from 2026-10-07 to 2026-10-09 for 2 adults?")

    assert reply.type == "availability"
    assert reply.availability.nights == 2


def test_availability_uses_booking_context_for_follow_up(offline):
    context = BookingContext(check_in="2026-10-07", check_out="2026-10-08", adults=2)
    reply = ask(offline, "What about availability for 3 adults?", booking_context=context)

    assert reply.type == "availability"
    assert reply.availability.adults == 3


def test_availability_with_past_dates_returns_form_error(offline):
    reply = ask(offline, "Book a room from 2026-01-01 to 2026-01-03 for 2 adults")

    assert reply.type == "collect_booking_details"
    assert "past" in reply.form_error


@pytest.mark.parametrize(
    ("question", "source_id"),
    [
        ("What is the cancellation policy for my booking?", "policies.cancellation"),
        ("Can I change my booking?", "policies.cancellation"),
        ("Is breakfast available?", "amenities.dining"),
        ("Is parking available?", "amenities.parking"),
    ],
)
def test_faq_questions_mentioning_booking_or_available_are_not_treated_as_availability(offline, question, source_id):
    reply = ask(offline, question)

    assert reply.type == "answer"
    assert source_id in [s.id for s in reply.sources]


def test_guest_count_follow_up_uses_dates_from_booking_context(offline):
    context = BookingContext(check_in="2026-10-07", check_out="2026-10-09")
    reply = ask(offline, "3 adults.", booking_context=context)

    assert reply.type == "availability"
    assert (reply.availability.adults, reply.availability.nights) == (3, 2)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("room for three guests", 3), ("we are 4 people", 4), ("2 adults", 2), ("a room please", None)],
)
def test_extract_party_size(text, expected):
    assert extract_party_size(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Do you have rooms available on Friday?", True),
        ("I want to book a room", True),
        ("Which room is suitable for three guests?", False),
        ("What time is check-in?", False),
    ],
)
def test_availability_intent_detection(text, expected):
    assert is_availability_request(text) is expected
