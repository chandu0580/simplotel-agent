"""Deterministic conversational layer.

A guest assistant is not a search box: "hi", "how can you help?", "how are you?", "thanks" and "bye"
are part of a normal conversation and must never be answered with "I couldn't find that in our hotel
information".

These intents are handled here, before any retrieval, model call or tool:

* they are cheap and deterministic (no tokens, no latency),
* they behave identically whether AI is enabled or not,
* they keep one voice, instead of the model improvising a persona ("I'm doing great! 😊"),
* they assert no hotel facts, so nothing can be hallucinated.

Replies are deliberately one or two sentences: a guest wants an answer, not an essay.

A message only counts as conversational when that is *all* it is. Anything carrying hotel substance
("hi, is breakfast included?") falls through to the normal routing, so the guest still gets a grounded
answer. Availability is never triggered from here: the booking form belongs to the availability intent.
"""

import re
from typing import Literal

from ..knowledge.models import KnowledgeBase
from ..schemas import ChatReply

ConversationalIntent = Literal["greeting", "capability", "smalltalk", "thanks", "goodbye"]

# Any hotel subject in the message means it is a real question, not small talk.
HOTEL_SUBJECT = re.compile(
    r"\b(room|rooms|suite|villa|bed|beds|breakfast|dinner|lunch|food|meal|restaurant|bar|pool|gym|spa|wifi|wi-fi|internet|"
    r"park|parking|check[\s-]?in|check[\s-]?out|checkin|checkout|cancel\w*|refund|polic\w+|pet|pets|dog|smok\w+|child|children|kid|kids|"
    r"adult|adults|guest|guests|price|prices|rate|rates|cost|costs|cheap|tariff|availab\w+|book\w*|reserv\w+|stay|night|nights|date|dates|"
    r"weekend|tomorrow|today|amenit\w+|facilit\w+|beach|airport|transfer|taxi|shuttle|towel|balcony|view|ac|air con\w*|laundry|"
    r"early|late|luggage|address|location|contact|phone|email|discount|offer|offers)\b",
    re.I,
)

_GREETING = re.compile(r"^(hi+|hey+|hello+|hola|yo|namaste|namaskar|greetings|good\s+(morning|afternoon|evening|day))\b", re.I)
_CAPABILITY = re.compile(
    r"\b(how\s+(can|could|do)\s+you\s+help|what\s+(can|could)\s+you\s+(do|help)|what\s+can\s+i\s+ask|what\s+do\s+you\s+(do|help|know)|"
    r"what\s+(kind\s+of\s+)?(information|info)\s+do\s+you\s+have|can\s+you\s+help(\s+me)?|tell\s+me\s+what\s+you\s+can\s+do|"
    r"who\s+are\s+you|what\s+are\s+you|are\s+you\s+(a\s+)?(bot|human|ai|real)|how\s+do\s+you\s+work|what\s+is\s+this)\b",
    re.I,
)
# Everyday pleasantries that are neither greeting nor capability question.
_SMALLTALK = re.compile(
    r"\b(how\s+are\s+you|how\s+are\s+u|how'?s\s+it\s+going|how\s+do\s+you\s+do|hope\s+you'?re\s+well|"
    r"are\s+you\s+(there|ok|okay|fine|busy)|can\s+i\s+ask\s+(you\s+)?(something|a\s+question|anything)|"
    r"may\s+i\s+ask|nice\s+to\s+meet\s+you|good\s+to\s+see\s+you|what'?s\s+up|sup)\b",
    re.I,
)
_THANKS = re.compile(r"^(thanks?|thank\s+you|thanks\s+a\s+lot|thx|ty|cheers|great|perfect|awesome|excellent|nice|cool|lovely|ok|okay|okey|k|alright|"
                     r"got\s+it|understood|sure|fine|that'?s?\s+(helpful|great|perfect|good|nice)|good\s+to\s+know|no\s+problem)\b", re.I)
_GOODBYE = re.compile(r"^(bye+|goodbye|good\s*bye|see\s+you|see\s+ya|cya|take\s+care|good\s*night|farewell|that'?s?\s+all|nothing\s+else)\b", re.I)


def _normalise(text: str) -> str:
    """Trim punctuation and emoji so 'Hi!! 👋' and 'hi' classify the same."""
    return re.sub(r"[^\w\s'-]", " ", text or "").strip()


def _is_whole_message(pattern: re.Pattern[str], cleaned: str) -> bool:
    match = pattern.match(cleaned)
    return bool(match) and match.end() == len(cleaned)


def classify(text: str) -> ConversationalIntent | None:
    """The intent when the message is *only* small talk, otherwise None."""
    cleaned = _normalise(text)
    if not cleaned or len(cleaned.split()) > 12:
        return None
    # A phrase that is small talk from beginning to end wins outright, even when it happens to contain a
    # hotel word ("good night", "that's all").
    for pattern, intent in ((_GOODBYE, "goodbye"), (_THANKS, "thanks"), (_GREETING, "greeting")):
        if _is_whole_message(pattern, cleaned):
            return intent  # type: ignore[return-value]
    if HOTEL_SUBJECT.search(cleaned):
        return None  # carries a real question: let the normal routing answer it
    if _CAPABILITY.search(cleaned):
        return "capability"
    if _SMALLTALK.search(cleaned):
        return "smalltalk"
    if _GOODBYE.match(cleaned):
        return "goodbye"
    if _GREETING.match(cleaned):
        # "hi" and "hi there" are greetings; "hi, I arrive on Friday" is not (it carries a real question).
        return "greeting" if len(cleaned.split()) <= 4 else None
    if _THANKS.match(cleaned):
        return "thanks"
    return None


def reply_for(intent: ConversationalIntent, kb: KnowledgeBase, suggestions: list[str]) -> ChatReply:
    """A short, natural reply that states no hotel facts beyond the property's own name."""
    hotel = kb.hotel.name
    texts: dict[str, str] = {
        "greeting": f"Hello, and welcome to {hotel}. 👋 What can I help you with?",
        "capability": (
            "I can help with rooms, amenities, breakfast, check-in and check-out, hotel policies "
            "and room availability. What would you like to know?"
        ),
        "smalltalk": f"All good here, thanks! What can I help you with at {hotel}?",
        "thanks": "You're welcome! Anything else about your stay?",
        "goodbye": f"Thank you for visiting {hotel} — have a lovely stay! 🌴",
    }
    # Starter chips help when the guest hasn't asked anything yet; after "thanks" or "bye" they'd be pushy.
    with_chips = intent in ("greeting", "capability", "smalltalk")
    return ChatReply(type="clarification", text=texts[intent], suggestions=suggestions if with_chips else [])
