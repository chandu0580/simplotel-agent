"""Guardrails around the model.

    guest input → InputGuardrails (exfiltration block, injection flags, tag neutralisation)
      → model → tool authorization (ToolRegistry)
      → OutputGuardrails (secrets, prompt leakage, citations, prices, availability claims) → guest

Deterministic checks catch what can be checked reliably. They don't replace semantic grounding
evaluation (see the eval suite); they stop the failures that must never reach a guest.
"""

from dataclasses import dataclass, field
import re

from ..core.observability import Redactor
from ..knowledge.models import KnowledgeBase
from ..schemas import BookingPrefill, ChatReply, Source
from .prompts import PROMPT_LEAK_MARKERS

MAX_SUGGESTIONS = 3

_EXFILTRATION = re.compile(
    r"\b(reveal|show|print|repeat|display|output|give|tell|share|leak|dump|what\s+(is|are))\b[^.?!\n]{0,60}?"
    r"\b(system\s+(prompt|instructions?|message)|developer\s+(prompt|message|instructions?)|"
    r"your\s+(hidden\s+|initial\s+|original\s+|internal\s+)?(instructions|prompt|rules|configuration)|"
    r"api[\s_-]?keys?|access\s+tokens?|secret\s+keys?|environment\s+variables|\.env\b)",
    re.IGNORECASE,
)
_INJECTION_FLAGS = {
    "ignore_instructions": re.compile(r"\b(ignore|disregard|forget)\b[^.?!\n]{0,30}\b(previous|prior|above|earlier|all|your)\b[^.?!\n]{0,20}\b(instructions|rules|prompts?)\b", re.I),
    "role_override": re.compile(r"\b(you are now|act as|developer mode|jailbreak|DAN mode)\b", re.I),
    "policy_override": re.compile(r"\bpretend\b[^.?!\n]{0,40}\b(policy|policies|rules?|price|rate)\b", re.I),
    "tool_coercion": re.compile(r"\b(call|use|run|invoke)\b[^.?!\n]{0,30}\b(tool|function|booking api|create_booking)\b", re.I),
}
_PROMPT_TAGS = re.compile(r"</?\s*(guest_message|context|system|hotel_knowledge_base)\s*>", re.I)


def neutralise_prompt_tags(text: str) -> str:
    """Stop guest-controlled text (current message or replayed history) from closing/opening prompt sections."""
    return _PROMPT_TAGS.sub(lambda m: m.group(0).replace("<", "‹").replace(">", "›"), text)

_CURRENCY_AMOUNT = re.compile(r"(?:₹|\bINR\b|\bRs\.?)\s?(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s?(?:rupees|\bINR\b)", re.I)
_AVAILABILITY_CLAIM = re.compile(
    r"\b\d+\s+rooms?\s+(left|remaining|available)\b|\bsold\s+out\b|\bfully\s+booked\b|"
    r"\b(every|all(\s+of\s+our)?)\s+rooms?\s+(is|are)\s+available\b|"
    r"\b(we\s+(still\s+)?have|there\s+(is|are))\s+(availability|rooms?\s+available)\b",
    re.I,
)


@dataclass
class InputVerdict:
    text: str  # sanitised text to send to the model
    blocked: bool = False
    flags: list[str] = field(default_factory=list)
    reply: ChatReply | None = None


class InputGuardrails:
    def check(self, message: str) -> InputVerdict:
        flags = [name for name, pattern in _INJECTION_FLAGS.items() if pattern.search(message)]
        sanitised = message
        if _PROMPT_TAGS.search(message):
            flags.append("prompt_tag_injection")
            sanitised = neutralise_prompt_tags(message)
        if _EXFILTRATION.search(message):
            reply = ChatReply(
                type="clarification",
                text="I can't share internal instructions or configuration, but I'm happy to help with questions about the hotel or to check room availability.",
                suggestions=["What time is check-in?", "Check room availability"],
            )
            return InputVerdict(sanitised, True, flags + ["exfiltration_attempt"], reply)
        return InputVerdict(sanitised, False, flags)


def numbers_in(text: str) -> set[int]:
    return {int(float(n.replace(",", ""))) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text) if n.replace(",", "").replace(".", "").isdigit()}


@dataclass
class GuardrailOutcome:
    reply: ChatReply
    triggered: list[str] = field(default_factory=list)


class OutputGuardrails:
    def __init__(self, redactor: Redactor, price_check_enabled: bool = True):
        self.redactor = redactor
        self.price_check_enabled = price_check_enabled  # default; a tenant override is passed per call

    @staticmethod
    def _unsupported_claim(text: str) -> bool:
        """For model text with no citations: any inventory statement or price is unsupported."""
        return bool(_AVAILABILITY_CLAIM.search(text) or _CURRENCY_AMOUNT.search(text))

    def _safe_fallback(self, kb: KnowledgeBase, text: str) -> ChatReply:
        return ChatReply(type="fallback", text=f"{text} Our front desk can help 24/7: {kb.contact_line()}.", suggestions=[])

    def _leaks(self, text: str) -> str | None:
        if self.redactor.redact_text(text) != text:
            return "secret_leak"
        if any(marker in text for marker in PROMPT_LEAK_MARKERS):
            return "prompt_leak"
        return None

    def check_answer(
        self,
        kb: KnowledgeBase,
        reply_type: str,
        text: str,
        source_ids: list[str],
        suggestions: list[str],
        price_check_enabled: bool | None = None,
        quoted_prices: set[int] | None = None,
    ) -> GuardrailOutcome:
        """`quoted_prices` are figures the guest was already shown by a real availability search.

        Without them the guard only trusts knowledge-base rates, which are indicative "from" rates:
        a correct answer repeating the live price the guest just saw would be replaced by a fallback,
        and the model would be pushed towards quoting the cheaper indicative rate instead.
        """
        text = text.strip()
        suggestions = [
            s.strip() for s in suggestions if s.strip() and len(s) <= 80 and not self._leaks(s) and not self._unsupported_claim(s)
        ][:MAX_SUGGESTIONS]
        price_check = self.price_check_enabled if price_check_enabled is None else price_check_enabled

        leak = self._leaks(text)
        if leak:
            return GuardrailOutcome(self._safe_fallback(kb, "I'm sorry, I can't help with that."), [leak])

        cited = [kb.entry(i) for i in dict.fromkeys(source_ids)]
        unknown = [i for i, e in zip(dict.fromkeys(source_ids), cited, strict=True) if e is None]
        cited = [e for e in cited if e is not None]
        triggered = ["unknown_source"] if unknown else []
        sources = [Source(id=e.id, title=e.title) for e in cited]

        if reply_type == "answer" and not sources:
            return GuardrailOutcome(self._safe_fallback(kb, "I'm sorry, I couldn't find a reliable answer to that in our hotel information."), triggered + ["uncited_answer"])

        if _AVAILABILITY_CLAIM.search(text):
            # Inventory statements must come from check_availability, never from free text.
            return GuardrailOutcome(
                ChatReply(
                    type="collect_booking_details",
                    text="I can check live availability for you. Please choose your dates and number of guests.",
                    booking_prefill=BookingPrefill(),
                ),
                triggered + ["availability_claim"],
            )

        if price_check:
            amounts = {int(float((a or b).replace(",", ""))) for a, b in _CURRENCY_AMOUNT.findall(text)}
            allowed: set[int] = set(quoted_prices or ())
            for entry in cited:
                allowed |= numbers_in(entry.content)
            # An amount the answer didn't cite is either *under-cited* or invented, and the two deserve
            # different treatment. A guest asking "is breakfast included in that room?" gets an answer
            # about the room that quotes the breakfast supplement from the dining entry; discarding it as
            # an unconfirmed price told the guest we had nothing, which is worse than the citation gap.
            # So: if a published entry carries the figure, cite that entry and keep the answer. If none
            # does, the figure came from the model, and the fallback stands.
            for amount in sorted(amounts - allowed):
                source = next((e for e in kb.entries if amount in numbers_in(e.content)), None)
                if source is None:
                    return GuardrailOutcome(self._safe_fallback(kb, "I couldn't confirm that price from our hotel information."), triggered + ["unsupported_price"])
                if source.id not in {s.id for s in sources}:
                    sources.append(Source(id=source.id, title=source.title))
                    triggered.append("price_source_added")

        if reply_type == "fallback" and kb.hotel.phone not in text:
            text = f"{text} Our front desk can help 24/7: {kb.contact_line()}."
        return GuardrailOutcome(ChatReply(type=reply_type, text=text, sources=sources, suggestions=suggestions), triggered)

    def check_model_text(self, kb: KnowledgeBase, text: str | None) -> tuple[str | None, list[str]]:
        """For short model-written text attached to tool results (e.g. the booking form message)."""
        if text and (leak := self._leaks(text)):
            return None, [leak]
        if text and self._unsupported_claim(text):
            return None, ["unsupported_claim"]
        return text, []
