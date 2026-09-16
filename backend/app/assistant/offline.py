"""Deterministic assistant used when the LLM is disabled or failing.

It is intentionally conservative: it answers only when a knowledge-base entry clearly matches,
and otherwise returns a fallback with contact details. Availability goes through the same tool
pipeline (validation, timeout, audit) as model-requested calls.
"""

from datetime import date
import re
import time

from ..core.tracing import AITrace
from ..knowledge.retrieval import KeywordRetriever
from ..schemas import AvailabilityResult, BookingContext, BookingPrefill, ChatReply, Source
from ..tools.base import ToolContext, ToolRegistry
from ..tools.builtin import NeedsDetails
from .agent import availability_unavailable_reply, dependency_reason
from .turn import DependencyUnavailable, Turn

# "Available"/"book" alone are too broad ("Is breakfast available?", "cancel my booking"),
# so they only count as availability intent when the message is about rooms/stays or matches no
# FAQ topic. The explicit room phrases always count.
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


class OfflineAssistant:
    def __init__(self, tools: ToolRegistry, retriever: KeywordRetriever):
        self.tools = tools
        self.retriever = retriever

    def reply(self, turn: Turn, trace: AITrace) -> ChatReply:
        text = turn.message
        context = turn.request.booking_context
        retrieval_started = time.perf_counter()
        evidence = self.retriever.retrieve(turn.kb, text)
        trace.retrieval_latency_ms = round((time.perf_counter() - retrieval_started) * 1000, 3)
        trace.evidence_ids = evidence.ids
        best_score = evidence.evidence[0].score if evidence.evidence else 0

        if is_availability_request(text, matches_faq=best_score > 0):
            return self._availability(turn, trace, text, context)

        party = extract_party_size(text)
        # Follow-up to a booking form/search: "3 adults." with dates already known.
        if party is not None and context and context.check_in and context.check_out and best_score == 0:
            return self._availability(turn, trace, text, context)
        if party is not None and re.search(r"\broom|suite|villa|stay|accommodat|fit|sleep", text.lower()):
            return self._rooms_for_party(turn, trace, party)

        if best_score == 0:
            return self.fallback(turn)
        matches = [e for e in evidence.evidence if e.score == best_score][:2]
        trace.cited_ids = [e.id for e in matches]
        return ChatReply(
            type="answer",
            text="\n\n".join(e.content for e in matches),
            sources=[Source(id=e.id, title=e.title) for e in matches],
            suggestions=DEFAULT_SUGGESTIONS,
        )

    def _availability(self, turn: Turn, trace: AITrace, text: str, context: BookingContext | None) -> ChatReply:
        dates = extract_dates(text)
        prefill = BookingPrefill(
            check_in=dates[0] if dates else (context.check_in if context else None),
            check_out=dates[1] if len(dates) > 1 else (context.check_out if context else None),
            adults=extract_party_size(text) or (context.adults if context else None),
            children=context.children if context else None,
        )
        if not (prefill.check_in and prefill.check_out and prefill.adults):
            return ChatReply(
                type="collect_booking_details",
                text="I can check that for you. Please choose your check-in and check-out dates and the number of guests.",
                booking_prefill=prefill,
            )
        ctx = ToolContext(tenant=turn.tenant, kb=turn.kb, today=turn.today, booking_context=context, tenant_flags=turn.tenant_flags)
        args = {"check_in": prefill.check_in.isoformat(), "check_out": prefill.check_out.isoformat(), "adults": prefill.adults, "children": prefill.children or 0}
        result = self.tools.execute("check_availability", args, ctx, invoked_by="system")
        trace.tool_calls.append(result.record("system"))
        if not result.ok:
            if reason := dependency_reason(result.error_code):
                raise DependencyUnavailable(availability_unavailable_reply(turn), reason)
            return self.fallback(turn)
        if isinstance(result.data, NeedsDetails):
            return ChatReply(type="collect_booking_details", text="Let's fix the booking details.", booking_prefill=prefill, form_error=result.data.form_error)
        assert isinstance(result.data, AvailabilityResult)
        return ChatReply(type="availability", text=result.data.message, availability=result.data, suggestions=["What is the cancellation policy?", "Is breakfast included?"])

    def _rooms_for_party(self, turn: Turn, trace: AITrace, party: int) -> ChatReply:
        kb = turn.kb
        fitting = [r for r in kb.rooms if r.max_occupancy >= party]
        # Room data is looked up directly, so the room entries are both the evidence and the citations.
        trace.evidence_ids = [f"rooms.{r.id}" for r in kb.rooms]
        trace.cited_ids = [f"rooms.{r.id}" for r in (fitting or kb.rooms)]
        if not fitting:
            largest = max(r.max_occupancy for r in kb.rooms)
            return ChatReply(
                type="fallback",
                text=f"None of our rooms sleeps {party} guests on its own (the largest sleeps {largest}), so you would need more than one room. Please {kb.contact_line()} for group bookings.",
                sources=[Source(id=f"rooms.{r.id}", title=r.name) for r in kb.rooms],
                suggestions=["Check room availability"],
            )
        lines = [
            f"- {r.name}: sleeps up to {r.max_occupancy} (max {r.max_adults} adults), {r.beds}, "
            f"breakfast {'included' if r.breakfast_included else 'not included'}, from {kb.hotel.currency} {r.base_rate:,}/night"
            for r in fitting
        ]
        return ChatReply(
            type="answer",
            text=f"These rooms can accommodate {party} guests:\n" + "\n".join(lines),
            sources=[Source(id=f"rooms.{r.id}", title=r.name) for r in fitting],
            suggestions=["Check room availability", "Can I get an extra bed?"],
        )

    def fallback(self, turn: Turn) -> ChatReply:
        contact = turn.kb.entry("contact.front_desk")
        return ChatReply(
            type="fallback",
            text=f"I'm sorry, I don't have reliable information about that. Our front desk can help 24/7: {turn.kb.contact_line()}.",
            sources=[Source(id=contact.id, title=contact.title)] if contact else [],
            suggestions=DEFAULT_SUGGESTIONS,
        )
