"""Personal-data minimisation for guest text.

Guest messages are sent to the model provider and stored in conversation state (up to the conversation
TTL). No feature of the assistant needs payment cards, email addresses or phone numbers: it can't book,
charge or call anyone. So they are masked before the text reaches the model, storage or logs.

* Payment card numbers: 13-19 digits (spaces/dashes allowed) that pass the Luhn check. Always masked.
* Email addresses and phone numbers: masked by default (PII_MASK_CONTACT_DETAILS=true).

Masking is conservative pattern matching, not a guarantee: names, addresses and free-text identifiers
are not detected. That residual risk is recorded in docs/PRIVACY.md.
"""

from dataclasses import dataclass, field
import re

CARD = "[card number removed]"
EMAIL = "[email removed]"
PHONE = "[phone number removed]"

_CARD_CANDIDATE = re.compile(r"(?<![\d])(?:\d[ \-]?){12,18}\d(?![\d])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# International or local numbers with at least 8 digits; dates (2026-10-07) and prices are excluded by shape.
_PHONE = re.compile(r"(?<![\w-])\+?\d[\d ()\-]{6,}\d(?![\w-])")
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{2}[-/]\d{2}[-/]\d{2,4}$")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


@dataclass
class Minimised:
    text: str
    masked: list[str] = field(default_factory=list)  # kinds only, never values


def minimise(text: str, *, mask_contact_details: bool = True) -> Minimised:
    masked: list[str] = []

    def card(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            masked.append("card")
            return CARD
        return match.group(0)

    text = _CARD_CANDIDATE.sub(card, text)
    if mask_contact_details:

        def email(_match: re.Match) -> str:
            masked.append("email")
            return EMAIL

        def phone(match: re.Match) -> str:
            value = match.group(0).strip()
            if _DATE_LIKE.match(value) or not 8 <= len(re.sub(r"\D", "", value)) <= 15:  # E.164 allows at most 15 digits
                return match.group(0)
            masked.append("phone")
            return PHONE

        text = _EMAIL.sub(email, text)
        text = _PHONE.sub(phone, text)
    return Minimised(text, masked)
