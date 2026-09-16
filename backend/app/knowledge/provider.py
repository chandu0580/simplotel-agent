"""Knowledge provider boundary.

The assistant depends on `KnowledgeProvider`, not on where content lives. `JsonKnowledgeProvider`
reads per-hotel JSON files (development and demo). A database- or CMS-backed provider would
implement the same three methods.
"""

from datetime import date
import json
from pathlib import Path
from typing import Protocol

from ..core.cache import Cache
from ..core.errors import AppError, ErrorCode
from ..core.versioning import content_hash
from .models import HotelProfile, KnowledgeBase, KnowledgeEntry, Room


class KnowledgeProvider(Protocol):
    def snapshot(self, hotel_id: str, as_of: date) -> KnowledgeBase: ...
    def list_entries(self, hotel_id: str, include_unpublished: bool = False) -> list[KnowledgeEntry]: ...
    def profile(self, hotel_id: str) -> HotelProfile: ...
    def is_healthy(self, hotel_ids: list[str]) -> bool: ...


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
        source="rooms",
    )


def parse_hotel_file(path: Path) -> tuple[HotelProfile, list[Room], list[KnowledgeEntry]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    hotel = HotelProfile(**raw["hotel"])
    rooms = [Room(**r) for r in raw["rooms"]]
    entries = [KnowledgeEntry(**e) for e in raw["knowledge"]]
    entries += [_room_entry(r, hotel.currency) for r in rooms]
    ids = [e.id for e in entries]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate knowledge entry ids in {path}")
    return hotel, rooms, entries


def build_snapshot(hotel: HotelProfile, rooms: list[Room], entries: list[KnowledgeEntry], as_of: date) -> KnowledgeBase:
    servable = [e for e in entries if e.is_servable(as_of)]
    version = content_hash(
        {
            "hotel": hotel.model_dump(mode="json"),
            "rooms": [r.model_dump(mode="json") for r in rooms],
            "entries": [(e.id, e.version, e.content) for e in servable],
        }
    )
    return KnowledgeBase(hotel=hotel, rooms=rooms, entries=servable, as_of=as_of, knowledge_version=version)


def load_knowledge_base(path: Path, as_of: date) -> KnowledgeBase:
    return build_snapshot(*parse_hotel_file(path), as_of=as_of)


class JsonKnowledgeProvider:
    def __init__(self, data_dir: Path, cache: Cache, ttl_seconds: int):
        self.hotels_dir = data_dir / "hotels"
        self.cache = cache
        self.ttl = ttl_seconds

    def _hotel_path(self, hotel_id: str) -> Path:
        # hotel_id is validated against the tenant registry before reaching here; this guards
        # against path traversal regardless.
        if not hotel_id.replace("-", "").replace("_", "").isalnum():
            raise AppError(ErrorCode.HOTEL_NOT_FOUND, "Hotel not found.", 404)
        return self.hotels_dir / hotel_id / "hotel.json"

    def _load(self, hotel_id: str) -> tuple[HotelProfile, list[Room], list[KnowledgeEntry]]:
        key = f"knowledge:{hotel_id}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        path = self._hotel_path(hotel_id)
        if not path.exists():
            raise AppError(ErrorCode.HOTEL_NOT_FOUND, "Hotel not found.", 404)
        parsed = parse_hotel_file(path)
        if parsed[0].id != hotel_id:
            raise ValueError(f"{path} declares hotel id {parsed[0].id!r}, expected {hotel_id!r}")
        self.cache.set(key, parsed, self.ttl)
        return parsed

    def snapshot(self, hotel_id: str, as_of: date) -> KnowledgeBase:
        key = f"snapshot:{hotel_id}:{as_of.isoformat()}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        snapshot = build_snapshot(*self._load(hotel_id), as_of=as_of)
        self.cache.set(key, snapshot, self.ttl)
        return snapshot

    def list_entries(self, hotel_id: str, include_unpublished: bool = False) -> list[KnowledgeEntry]:
        _, _, entries = self._load(hotel_id)
        return entries if include_unpublished else [e for e in entries if e.status == "published"]

    def profile(self, hotel_id: str) -> HotelProfile:
        return self._load(hotel_id)[0]

    def is_healthy(self, hotel_ids: list[str]) -> bool:
        try:
            for hotel_id in hotel_ids:
                self._load(hotel_id)
            return True
        except Exception:
            return False
