"""Hotel configuration and knowledge content models."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Brand(BaseModel):
    assistant_name: str = "Guest Assistant"
    primary_color: str = "#0f5d4e"


class HotelProfile(BaseModel):
    id: str
    name: str
    tagline: str
    address: str
    city: str = ""
    phone: str
    email: str
    whatsapp: str
    currency: str
    timezone: str
    check_in_time: str
    check_out_time: str
    languages: list[str] = Field(default_factory=lambda: ["en"])
    brand: Brand = Field(default_factory=Brand)


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


class ContentStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class KnowledgeEntry(BaseModel):
    id: str
    topic: str
    title: str
    content: str
    keywords: list[str] = Field(default_factory=list)
    # Content lifecycle: only published entries inside their effective window reach the assistant.
    status: ContentStatus = ContentStatus.PUBLISHED
    version: int = 1
    source: str = "hotel.json"
    updated_at: datetime | None = None
    updated_by: str | None = None
    effective_from: date | None = None
    effective_until: date | None = None

    def is_servable(self, as_of: date) -> bool:
        if self.status != ContentStatus.PUBLISHED:
            return False
        if self.effective_from and as_of < self.effective_from:
            return False
        if self.effective_until and as_of > self.effective_until:
            return False
        return True


@dataclass(frozen=True)
class KnowledgeBase:
    """Immutable snapshot of one hotel's servable content on a given business date."""

    hotel: HotelProfile
    rooms: list[Room]
    entries: list[KnowledgeEntry]
    as_of: date
    knowledge_version: str
    _by_id: dict[str, KnowledgeEntry] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {e.id: e for e in self.entries})

    def entry(self, entry_id: str) -> KnowledgeEntry | None:
        return self._by_id.get(entry_id)

    def room(self, room_id: str) -> Room | None:
        return next((r for r in self.rooms if r.id == room_id), None)

    def contact_line(self) -> str:
        h = self.hotel
        return f"call {h.phone}, WhatsApp {h.whatsapp} or email {h.email}"
