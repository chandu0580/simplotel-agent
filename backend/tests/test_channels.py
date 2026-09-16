"""One channel-independent reply, rendered for web, WhatsApp and voice."""

from datetime import date

from app.channels.adapters import ADAPTERS, WHATSAPP_BUTTON_MAX_CHARS, WHATSAPP_MAX_BUTTONS
from app.schemas import AvailabilityResult, BookingPrefill, ChatReply, RoomOffer
from app.tenancy import Channel


def room(room_id: str, name: str, total: int, left: int) -> RoomOffer:
    return RoomOffer(room_id=room_id, name=name, description="", beds="1 king", size_sqm=30, max_occupancy=2, breakfast_included=True, rooms_left=left,
                     nightly_rate=total // 2, total_price=total, currency="INR", features=[])


AVAILABILITY = ChatReply(
    type="availability",
    text="3 room types available for 2 adults, 2 nights.",
    availability=AvailabilityResult(check_in=date(2026, 10, 7), check_out=date(2026, 10, 9), nights=2, adults=2, children=0, available=True,
                                    rooms=[room("a", "Garden Room", 10400, 12), room("b", "Deluxe Pool View Room", 15600, 2), room("c", "Ocean Villa", 42000, 1)],
                                    message="3 room types available"),
    suggestions=["What is the cancellation policy?", "Is breakfast included?", "Pool hours?", "Parking?"],
)
FORM = ChatReply(type="collect_booking_details", text="Which dates?", booking_prefill=BookingPrefill(), form_error="Check-in date cannot be in the past.")
LIST_ANSWER = ChatReply(type="answer", text="These rooms fit:\n- Deluxe Room\n- Family Suite\nEmail stay@hotel.example for groups.")


def test_web_gets_the_structured_reply():
    out = ADAPTERS[Channel.WEB].render(AVAILABILITY, "INR")
    assert out.structured["availability"]["rooms"][1]["rooms_left"] == 2


def test_whatsapp_renders_text_within_platform_limits():
    out = ADAPTERS[Channel.WHATSAPP].render(AVAILABILITY, "INR")
    assert "Deluxe Pool View Room: INR 15,600 total, breakfast included, only 2 left" in out.text
    assert len(out.quick_replies) <= WHATSAPP_MAX_BUTTONS and all(len(b) <= WHATSAPP_BUTTON_MAX_CHARS for b in out.quick_replies)
    form = ADAPTERS[Channel.WHATSAPP].render(FORM, "INR")
    assert "reply with your check-in and check-out dates" in form.text and "cannot be in the past" in form.text
    assert out.structured is None


def test_voice_renders_short_speakable_sentences():
    out = ADAPTERS[Channel.VOICE].render(AVAILABILITY, "INR")
    assert "Garden Room at 10,400 rupees" in out.text and "Ocean Villa" not in out.text  # at most two options spoken
    assert "more options" in out.text
    spoken = ADAPTERS[Channel.VOICE].render(LIST_ANSWER, "INR").text
    assert "\n" not in spoken and "- " not in spoken and "@" not in spoken
