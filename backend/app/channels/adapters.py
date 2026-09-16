"""Channel adapters: render one channel-independent `ChatReply` for each channel.

The assistant core never produces UI-specific output. Web gets the structured reply (forms,
cards); WhatsApp gets text within its limits; voice gets short, speakable sentences. Inbound
webhooks (WhatsApp signature verification, telephony/STT) are not built; they would convert
provider payloads into `TurnRequest`s for the same `ConversationService`.
"""

from dataclasses import dataclass, field
import re
from typing import Protocol

from ..schemas import ChatReply
from ..tenancy import Channel

WHATSAPP_MAX_CHARS = 4096
WHATSAPP_MAX_BUTTONS = 3
WHATSAPP_BUTTON_MAX_CHARS = 20
VOICE_MAX_ROOMS = 2


@dataclass
class OutboundMessage:
    channel: Channel
    text: str
    quick_replies: list[str] = field(default_factory=list)
    structured: dict | None = None


class ChannelAdapter(Protocol):
    channel: Channel

    def render(self, reply: ChatReply, currency: str) -> OutboundMessage: ...


def _money(amount: int, currency: str) -> str:
    return f"{currency} {amount:,}"


class WebChannelAdapter:
    channel = Channel.WEB

    def render(self, reply: ChatReply, currency: str) -> OutboundMessage:
        return OutboundMessage(self.channel, reply.text, reply.suggestions, reply.model_dump(mode="json"))


class WhatsAppChannelAdapter:
    channel = Channel.WHATSAPP

    def render(self, reply: ChatReply, currency: str) -> OutboundMessage:
        lines = [reply.text]
        if reply.availability and reply.availability.rooms:
            for room in reply.availability.rooms:
                left = f", only {room.rooms_left} left" if room.rooms_left <= 3 else ""
                breakfast = ", breakfast included" if room.breakfast_included else ""
                lines.append(f"• {room.name}: {_money(room.total_price, room.currency)} total{breakfast}{left}")
            lines.append("Prices exclude taxes.")
        if reply.type == "collect_booking_details":
            # No date pickers in WhatsApp: ask for the details in one reply.
            lines.append("Please reply with your check-in and check-out dates and the number of guests, e.g. “12 Oct to 14 Oct, 2 adults”.")
            if reply.form_error:
                lines.insert(1, reply.form_error)
        text = "\n".join(lines)
        if len(text) > WHATSAPP_MAX_CHARS:
            text = text[: WHATSAPP_MAX_CHARS - 1] + "…"
        buttons = [s for s in reply.suggestions if len(s) <= WHATSAPP_BUTTON_MAX_CHARS][:WHATSAPP_MAX_BUTTONS]
        return OutboundMessage(self.channel, text, buttons)


class VoiceChannelAdapter:
    channel = Channel.VOICE

    def render(self, reply: ChatReply, currency: str) -> OutboundMessage:
        text = re.sub(r"^\s*[-•*]\s*", "", reply.text, flags=re.MULTILINE)  # no bullets when spoken
        text = re.sub(r"\s*\n+\s*", " ", text)
        text = re.sub(r"\S+@\S+", "our front desk email", text)  # emails are unusable when read aloud
        parts = [text.strip()]
        if reply.availability and reply.availability.rooms:
            rooms = reply.availability.rooms[:VOICE_MAX_ROOMS]
            spoken = [f"the {r.name} at {r.total_price:,} {'rupees' if r.currency == 'INR' else r.currency} in total" for r in rooms]
            parts.append("Options include " + " and ".join(spoken) + ".")
            if len(reply.availability.rooms) > VOICE_MAX_ROOMS:
                parts.append("There are more options if you'd like to hear them.")
        if reply.type == "collect_booking_details":
            parts.append("Which dates would you like, and how many guests?")
        return OutboundMessage(self.channel, " ".join(p for p in parts if p))


ADAPTERS: dict[Channel, ChannelAdapter] = {
    Channel.WEB: WebChannelAdapter(),
    Channel.API: WebChannelAdapter(),
    Channel.WHATSAPP: WhatsAppChannelAdapter(),
    Channel.VOICE: VoiceChannelAdapter(),
}
