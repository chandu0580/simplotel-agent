"""Deterministic mock availability service.

Everything that must be *correct* (date validation, occupancy fit, inventory,
pricing) lives here, never in the LLM. The LLM only decides *when* to call it.
"""

from datetime import date, timedelta
import json
from pathlib import Path

from pydantic import BaseModel

from ..knowledge.models import KnowledgeBase, Room
from ..schemas import AvailabilityResult, RoomOffer

MAX_NIGHTS = 30
MAX_DAYS_AHEAD = 365
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class AvailabilityValidationError(ValueError):
    """The request is well-formed but cannot be searched (e.g. past dates)."""


class WeekdayRule(BaseModel):
    weekday: str
    room_id: str
    booked: int


class SeasonalMultiplier(BaseModel):
    start: str  # MM-DD
    end: str  # MM-DD, may wrap past new year
    multiplier: float
    label: str

    def applies(self, day: date) -> bool:
        md = day.strftime("%m-%d")
        if self.start <= self.end:
            return self.start <= md <= self.end
        return md >= self.start or md <= self.end


class Inventory(BaseModel):
    total_rooms: dict[str, int]
    weekday_rules: list[WeekdayRule] = []
    booked: dict[str, dict[str, int]] = {}
    seasonal_multipliers: list[SeasonalMultiplier] = []

    def rooms_free(self, room_id: str, night: date) -> int:
        total = self.total_rooms.get(room_id, 0)
        booked = self.booked.get(night.isoformat(), {}).get(room_id, 0)
        weekday = WEEKDAYS[night.weekday()]
        booked += sum(r.booked for r in self.weekday_rules if r.weekday == weekday and r.room_id == room_id)
        return max(total - booked, 0)

    def season(self, night: date) -> SeasonalMultiplier | None:
        return next((s for s in self.seasonal_multipliers if s.applies(night)), None)


def load_inventory(path: Path) -> Inventory:
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("_comment", None)
    return Inventory(**raw)


def _fits(room: Room, adults: int, children: int) -> bool:
    return adults <= room.max_adults and children <= room.max_children and adults + children <= room.max_occupancy


def validate_search(check_in: date, check_out: date, adults: int, children: int, today: date) -> None:
    if check_in < today:
        raise AvailabilityValidationError("Check-in date cannot be in the past.")
    if check_out <= check_in:
        raise AvailabilityValidationError("Check-out date must be after the check-in date.")
    if (check_out - check_in).days > MAX_NIGHTS:
        raise AvailabilityValidationError(f"Online search supports stays of up to {MAX_NIGHTS} nights.")
    if check_in > today + timedelta(days=MAX_DAYS_AHEAD):
        raise AvailabilityValidationError("Bookings open 12 months in advance.")
    if adults < 1:
        raise AvailabilityValidationError("At least one adult is required.")
    if children < 0:
        raise AvailabilityValidationError("Number of children cannot be negative.")


def check_availability(
    check_in: date,
    check_out: date,
    adults: int,
    children: int = 0,
    *,
    today: date,
    kb: KnowledgeBase,
    inventory: Inventory,
) -> AvailabilityResult:
    validate_search(check_in, check_out, adults, children, today)

    nights = [check_in + timedelta(days=i) for i in range((check_out - check_in).days)]
    currency = kb.hotel.currency
    season_labels = {s.label for n in nights if (s := inventory.season(n))}

    fitting = [r for r in kb.rooms if _fits(r, adults, children)]
    offers: list[RoomOffer] = []
    sold_out: list[str] = []
    for room in sorted(fitting, key=lambda r: r.base_rate):
        rooms_left = min(inventory.rooms_free(room.id, n) for n in nights)
        if rooms_left == 0:
            sold_out.append(room.name)
            continue
        nightly_prices = []
        for n in nights:
            season = inventory.season(n)
            multiplier = season.multiplier if season else 1.0
            nightly_prices.append(int(round(room.base_rate * multiplier / 100.0)) * 100)
        total = sum(nightly_prices)
        offers.append(
            RoomOffer(
                room_id=room.id,
                name=room.name,
                description=room.description,
                beds=room.beds,
                size_sqm=room.size_sqm,
                max_occupancy=room.max_occupancy,
                breakfast_included=room.breakfast_included,
                rooms_left=rooms_left,
                nightly_rate=round(total / len(nights)),
                total_price=total,
                currency=currency,
                features=room.features,
            )
        )

    party = f"{adults} adult{'s' if adults != 1 else ''}"
    if children:
        party += f" and {children} child{'ren' if children != 1 else ''}"
    stay = f"{len(nights)} night{'s' if len(nights) != 1 else ''} from {check_in:%a %d %b %Y} to {check_out:%a %d %b %Y}"

    if offers:
        message = f"{len(offers)} room type{'s' if len(offers) != 1 else ''} available for {party}, {stay}."
    elif not fitting:
        largest = max(r.max_occupancy for r in kb.rooms)
        message = (
            f"No single room type can accommodate {party} (our largest room sleeps {largest}). "
            f"For larger groups, please book multiple rooms or contact us: {kb.contact_line()}."
        )
    else:
        message = f"Sorry, all suitable rooms are sold out for {party}, {stay}. Try different dates or contact us: {kb.contact_line()}."

    return AvailabilityResult(
        check_in=check_in,
        check_out=check_out,
        nights=len(nights),
        adults=adults,
        children=children,
        available=bool(offers),
        rooms=offers,
        sold_out_room_names=sold_out,
        message=message,
        season_label=", ".join(sorted(season_labels)) or None,
    )
