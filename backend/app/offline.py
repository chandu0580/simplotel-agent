"""Deterministic assistant used when the LLM is disabled or failing.

It is intentionally conservative: it answers only when a knowledge-base entry
clearly matches, and otherwise returns a fallback with contact details.
"""

from datetime import date
import re

from .availability import check_availability, AvailabilityValidationError
from .knowledge import KnowledgeBase, KnowledgeEntry
from .schemas import BookingContext, BookingPrefill, ChatReply, ChatRequest, Source

# "Available"/"book" alone are too broad ("Is breakfast available?", "cancel my booking"),
# so they only count as availability intent when the message is about rooms/stays or
# matches no FAQ topic. The explicit room phrases always count.
ROOM_AVAILABILITY_PATTERNS = [
    r"\bvacan",
    r"\bfree rooms?\b",
    r"\bany rooms?\b",
    r"\bdo you have (a |any )?rooms?\b",
    r"\brooms? (left|open)\b",
]
GENERIC_AVAILABILITY_PATTERNS = [r"\bavailab", r"\bbook(ing)?\b", r"\breserv"]
STAY_WORDS_RE = re.compile(r"\b(rooms?|suites?|villas?|stay|nights?|check[- ]?in date|dates?)\b")
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
PARTY_RE = re.compile(r"\b(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")\s+(guests?|people|persons?|adults?|pax|of us)\b")
ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
DEFAULT_SUGGESTIONS = ["What time is check-in?", "Is breakfast included?", "Check room availability"]


def is_availability_request(text: str, matches_faq: bool = False) -> bool:
    lowered = text.lower()
    if any(re.search(p, lowered) for p in ROOM_AVAILABILITY_PATTERNS) or len(extract_dates(text)) >= 2:
        return True
    if not any(re.search(p, lowered) for p in GENERIC_AVAILABILITY_PATTERNS):
        return False
    return bool(STAY_WORDS_RE.search(lowered) or extract_dates(text)) or not matches_faq


def extract_party_size(text: str) -> int | None:
    match = PARTY_RE.search(text.lower())
    if not match:
        return None
    raw = match.group(1)
    return int(raw) if raw.isdigit() else NUMBER_WORDS[raw]


def extract_dates(text: str) -> list[date]:
    found = []
    for raw in ISO_DATE_RE.findall(text):
        try:
            found.append(date.fromisoformat(raw))
        except ValueError:
            continue
    return found


def _keyword_score(entry: KnowledgeEntry, text: str) -> int:
    score = 0
    for kw in entry.keywords:
        if re.search(r"(?<![a-z])" + re.escape(kw.lower()) + r"(?![a-z])", text):
            # Multi-word phrases are stronger signals than single generic words.
            score += 2 if " " in kw or "-" in kw else 1
    return score


class OfflineAssistant:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def reply(self, request: ChatRequest, today: date) -> ChatReply:
        text = request.message
        context = request.booking_context
        if is_availability_request(text, matches_faq=self._best_faq_score(text) > 0):
            return self._availability(text, context, today)

        party = extract_party_size(text)
        # Follow-up to a booking form/search: "3 adults." with dates already known.
        if party is not None and context and context.check_in and context.check_out and self._best_faq_score(text) == 0:
            return self._availability(text, context, today)
        if party is not None and re.search(r"\broom|suite|villa|stay|accommodat|fit|sleep", text.lower()):
            return self._rooms_for_party(party)

        return self._faq(text)

    def _availability(self, text: str, context: BookingContext | None, today: date) -> ChatReply:
        dates = extract_dates(text)
        prefill = BookingPrefill(
            check_in=dates[0] if dates else (context.check_in if context else None),
            check_out=dates[1] if len(dates) > 1 else (context.check_out if context else None),
            adults=extract_party_size(text) or (context.adults if context else None),
            children=context.children if context else None,
        )
        if prefill.check_in and prefill.check_out and prefill.adults:
            try:
                result = check_availability(
                    prefill.check_in, prefill.check_out, prefill.adults, prefill.children or 0, today=today, kb=self.kb
                )
                return ChatReply(type="availability", text=result.message, availability=result, suggestions=["What is the cancellation policy?", "Is breakfast included?"])
            except AvailabilityValidationError as exc:
                return ChatReply(type="collect_booking_details", text="Let's fix the booking details.", booking_prefill=prefill, form_error=str(exc))
        return ChatReply(
            type="collect_booking_details",
            text="I can check that for you. Please choose your check-in and check-out dates and the number of guests.",
            booking_prefill=prefill,
        )

    def _rooms_for_party(self, party: int) -> ChatReply:
        fitting = [r for r in self.kb.rooms if r.max_occupancy >= party]
        if not fitting:
            largest = max(r.max_occupancy for r in self.kb.rooms)
            return ChatReply(
                type="fallback",
                text=f"None of our rooms sleeps {party} guests on its own (the largest sleeps {largest}), so you would need more than one room. Please {self.kb.contact_line()} for group bookings.",
                sources=[Source(id=f"rooms.{r.id}", title=r.name) for r in self.kb.rooms],
                suggestions=["Check room availability"],
            )
        lines = [
            f"- {r.name}: sleeps up to {r.max_occupancy} (max {r.max_adults} adults), {r.beds}, "
            f"breakfast {'included' if r.breakfast_included else 'not included'}, from {self.kb.hotel.currency} {r.base_rate:,}/night"
            for r in fitting
        ]
        return ChatReply(
            type="answer",
            text=f"These rooms can accommodate {party} guests:\n" + "\n".join(lines),
            sources=[Source(id=f"rooms.{r.id}", title=r.name) for r in fitting],
            suggestions=["Check room availability", "Can I get an extra bed?"],
        )

    def _faq(self, text: str) -> ChatReply:
        lowered = text.lower()
        scored = sorted(((self._score(e, lowered), e) for e in self.kb.entries), key=lambda s: -s[0])
        best_score = scored[0][0] if scored else 0
        if best_score == 0:
            return self.fallback()
        matches = [e for s, e in scored if s == best_score][:2]
        return ChatReply(
            type="answer",
            text="\n\n".join(e.content for e in matches),
            sources=[Source(id=e.id, title=e.title) for e in matches],
            suggestions=DEFAULT_SUGGESTIONS,
        )

    def _best_faq_score(self, text: str) -> int:
        lowered = text.lower()
        return max((self._score(e, lowered) for e in self.kb.entries), default=0)

    def _score(self, entry: KnowledgeEntry, text: str) -> int:
        # Room entries share generic keywords ("room", "price") and would match almost
        # everything, so only match them offline by their specific room name.
        if entry.topic == "rooms":
            return 3 if entry.title.lower() in text else 0
        return _keyword_score(entry, text)

    def fallback(self) -> ChatReply:
        return ChatReply(
            type="fallback",
            text=f"I'm sorry, I don't have reliable information about that. Our front desk can help 24/7: {self.kb.contact_line()}.",
            sources=[Source(id="contact.front_desk", title="Contacting the hotel")],
            suggestions=DEFAULT_SUGGESTIONS,
        )
