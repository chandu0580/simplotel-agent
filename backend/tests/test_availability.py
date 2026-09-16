from datetime import date, timedelta

import pytest

from app.availability import AvailabilityValidationError, check_availability
from tests.conftest import TODAY

WEDNESDAY = date(2026, 10, 7)
SATURDAY = date(2026, 10, 10)


def test_weekday_stay_returns_fitting_rooms_sorted_by_price(kb):
    result = check_availability(WEDNESDAY, WEDNESDAY + timedelta(days=2), adults=2, today=TODAY, kb=kb)

    assert result.available
    assert result.nights == 2
    assert [r.room_id for r in result.rooms] == ["garden-standard", "deluxe-pool-view", "family-suite", "ocean-villa"]
    garden = result.rooms[0]
    assert garden.nightly_rate == 5200
    assert garden.total_price == 10400
    assert garden.rooms_left == 12


def test_three_adults_excludes_rooms_that_cannot_fit_them(kb):
    result = check_availability(WEDNESDAY, WEDNESDAY + timedelta(days=1), adults=3, today=TODAY, kb=kb)

    assert {r.room_id for r in result.rooms} == {"deluxe-pool-view", "family-suite"}


def test_room_sold_out_on_any_night_is_excluded(kb):
    # Family suites are fully booked on Saturdays; a Fri–Sun stay includes Saturday night.
    result = check_availability(SATURDAY - timedelta(days=1), SATURDAY + timedelta(days=1), adults=4, children=1, today=TODAY, kb=kb)

    assert not result.available
    assert result.rooms == []
    assert result.sold_out_room_names == ["Family Suite"]
    assert "sold out" in result.message


def test_rooms_left_is_minimum_across_nights(kb):
    result = check_availability(SATURDAY - timedelta(days=1), SATURDAY + timedelta(days=1), adults=2, today=TODAY, kb=kb)

    deluxe = next(r for r in result.rooms if r.room_id == "deluxe-pool-view")
    assert deluxe.rooms_left == 2  # 8 rooms, 6 booked on Saturdays
    assert "Ocean Villa" in result.sold_out_room_names


def test_party_too_large_for_any_room_explains_group_option(kb):
    result = check_availability(WEDNESDAY, WEDNESDAY + timedelta(days=1), adults=7, today=TODAY, kb=kb)

    assert not result.available
    assert "No single room type" in result.message
    assert kb.hotel.phone in result.message


def test_peak_season_pricing_applies_multiplier(kb):
    check_in = date(2026, 12, 20)
    result = check_availability(check_in, check_in + timedelta(days=1), adults=2, today=TODAY, kb=kb)

    garden = next(r for r in result.rooms if r.room_id == "garden-standard")
    assert garden.total_price == 8300  # 5200 * 1.6 rounded to nearest 100
    assert result.season_label == "Peak season"


@pytest.mark.parametrize(
    ("check_in", "check_out", "adults", "error"),
    [
        (TODAY - timedelta(days=1), TODAY + timedelta(days=1), 2, "past"),
        (WEDNESDAY, WEDNESDAY, 2, "after the check-in"),
        (WEDNESDAY + timedelta(days=2), WEDNESDAY, 2, "after the check-in"),
        (WEDNESDAY, WEDNESDAY + timedelta(days=31), 2, "30 nights"),
        (TODAY + timedelta(days=400), TODAY + timedelta(days=401), 2, "12 months"),
        (WEDNESDAY, WEDNESDAY + timedelta(days=1), 0, "adult"),
    ],
)
def test_invalid_searches_are_rejected(kb, check_in, check_out, adults, error):
    with pytest.raises(AvailabilityValidationError, match=error):
        check_availability(check_in, check_out, adults, today=TODAY, kb=kb)
