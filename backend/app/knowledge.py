"""Loads the hotel knowledge base and exposes it as flat, citable entries."""

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

from pydantic import BaseModel

DATA_DIR = Path(__file__).parent / "data"


class HotelProfile(BaseModel):
    id: str
    name: str
    tagline: str
    address: str
    phone: str
    email: str
    whatsapp: str
    currency: str
    timezone: str
    check_in_time: str
    check_out_time: str


class Room(BaseModel):
    id: str
    name: str
    description: str
    size_sqm: int
    beds: str
    max_adults: int
    max_children: int
    max_occupancy: int
    extra_bed_allowed: bool
    base_rate: int
    breakfast_included: bool
    features: list[str]


class KnowledgeEntry(BaseModel):
    id: str
    topic: str
    title: str
    content: str
    keywords: list[str] = []


@dataclass(frozen=True)
class KnowledgeBase:
    hotel: HotelProfile
    rooms: list[Room]
    entries: list[KnowledgeEntry]

    def entry(self, entry_id: str) -> KnowledgeEntry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def room(self, room_id: str) -> Room | None:
        return next((r for r in self.rooms if r.id == room_id), None)

    def contact_line(self) -> str:
        h = self.hotel
        return f"call {h.phone}, WhatsApp {h.whatsapp} or email {h.email}"


def _room_entry(room: Room, currency: str) -> KnowledgeEntry:
    breakfast = "Breakfast included." if room.breakfast_included else "Breakfast not included."
    extra_bed = "Extra bed available." if room.extra_bed_allowed else "No extra beds."
    return KnowledgeEntry(
        id=f"rooms.{room.id}",
        topic="rooms",
        title=room.name,
        content=(
            f"{room.description} {room.size_sqm} sqm, {room.beds}. "
            f"Sleeps up to {room.max_occupancy} guests (max {room.max_adults} adults, "
            f"max {room.max_children} children). {breakfast} {extra_bed} "
            f"Rates from {currency} {room.base_rate:,} per night before taxes; the exact price "
            f"depends on dates. Features: {', '.join(room.features)}."
        ),
        keywords=[room.name.lower(), "room", "rooms", "suite", "villa", "guests", "people", "sleeps", "occupancy", "price", "rate"],
    )


def load_knowledge_base(path: Path = DATA_DIR / "hotel.json") -> KnowledgeBase:
    raw = json.loads(path.read_text(encoding="utf-8"))
    hotel = HotelProfile(**raw["hotel"])
    rooms = [Room(**r) for r in raw["rooms"]]
    entries = [KnowledgeEntry(**e) for e in raw["knowledge"]]
    entries += [_room_entry(r, hotel.currency) for r in rooms]
    ids = [e.id for e in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate knowledge entry ids in hotel.json")
    return KnowledgeBase(hotel=hotel, rooms=rooms, entries=entries)


@lru_cache
def get_knowledge_base() -> KnowledgeBase:
    return load_knowledge_base()
