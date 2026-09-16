import json

import pydantic
import pytest

from app.knowledge.provider import load_knowledge_base
from tests.conftest import GOA, TODAY


def test_every_entry_has_a_unique_citable_id(kb):
    ids = [e.id for e in kb.entries]
    assert len(ids) == len(set(ids))
    assert {f"rooms.{r.id}" for r in kb.rooms} <= set(ids)
    assert all(e.content.strip() for e in kb.entries)


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda d: d["knowledge"].append(dict(d["knowledge"][0])), ValueError),  # duplicate id
        (lambda d: d["rooms"][0].pop("max_occupancy"), pydantic.ValidationError),
        (lambda d: d.pop("hotel"), KeyError),
    ],
)
def test_malformed_knowledge_base_fails_fast_at_load(tmp_path, kb, mutate, error):
    """Bad KB data should stop the server at startup, not surface as wrong answers at request time."""
    from app.core.config import DEFAULT_DATA_DIR

    data = json.loads((DEFAULT_DATA_DIR / "hotels" / GOA / "hotel.json").read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "hotel.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(error):
        load_knowledge_base(path, TODAY)


# ---------- Content lifecycle ----------

from datetime import date  # noqa: E402

from app.knowledge.retrieval import FullContextRetriever, KeywordRetriever  # noqa: E402
from tests.conftest import answer_response, make_container, turn_request  # noqa: E402


def test_draft_and_expired_entries_are_not_served(offline_container):
    all_ids = {e.id for e in offline_container.knowledge.list_entries(GOA, include_unpublished=True)}
    served = offline_container.knowledge.snapshot(GOA, TODAY)

    assert {"amenities.rooftop_bar", "offers.monsoon_2026"} <= all_ids
    assert served.entry("amenities.rooftop_bar") is None  # draft
    assert served.entry("offers.monsoon_2026") is None  # expired 2026-08-31


def test_effective_window_controls_serving_and_knowledge_version(offline_container):
    during_offer = offline_container.knowledge.snapshot(GOA, date(2026, 8, 15))
    after_offer = offline_container.knowledge.snapshot(GOA, TODAY)

    assert during_offer.entry("offers.monsoon_2026") is not None
    assert during_offer.knowledge_version != after_offer.knowledge_version


def test_model_citing_unpublished_content_is_not_shown_to_guests(fake_messages):
    container = make_container(fake_messages)
    fake_messages.responses.append(
        answer_response({"type": "answer", "text": "Our rooftop bar serves cocktails until 1 AM.", "source_ids": ["amenities.rooftop_bar"], "suggestions": []})
    )

    outcome = container.assistant.handle(turn_request(container, "Is there a rooftop bar?"))

    assert outcome.reply.type == "fallback"
    assert "rooftop" not in outcome.reply.text.lower()
    assert "uncited_answer" in outcome.trace.guardrails and "unknown_source" in outcome.trace.guardrails


def test_unpublished_content_is_not_in_the_prompt(fake_messages):
    container = make_container(fake_messages)
    fake_messages.responses.append(answer_response({"type": "clarification", "text": "Hello!", "source_ids": [], "suggestions": []}))

    container.assistant.handle(turn_request(container, "hi"))

    system_prompt = fake_messages.calls[0]["system"][0]["text"]
    assert "Skyline rooftop bar" not in system_prompt and "Monsoon offer" not in system_prompt
    assert "[policies.cancellation]" in system_prompt


def test_retrievers(kb):
    full = FullContextRetriever().retrieve(kb, "anything")
    keyword = KeywordRetriever().retrieve(kb, "Can I cancel my booking and get a refund?")

    assert len(full.evidence) == len(kb.entries)
    assert keyword.ids[0] == "policies.cancellation"
    assert KeywordRetriever().retrieve(kb, "helipad").evidence == []


def test_unknown_or_malicious_hotel_ids_are_not_found(offline_container):
    from app.core.errors import AppError

    for hotel_id in ("hotel-nope", "../hotel-goa-001", r"..\secrets"):
        with pytest.raises(AppError):
            offline_container.knowledge.profile(hotel_id)
