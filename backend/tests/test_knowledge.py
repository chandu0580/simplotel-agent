import json

import pydantic
import pytest

from app.knowledge import load_knowledge_base


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
    from app.knowledge import DATA_DIR

    data = json.loads((DATA_DIR / "hotel.json").read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "hotel.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(error):
        load_knowledge_base(path)
