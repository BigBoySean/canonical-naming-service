import json
import re
from pathlib import Path

from canonical_naming.models import Entity

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed.json"
ID_PATTERN = re.compile(r"^ent_[a-z0-9_]+$")


def _load_seed() -> list[dict]:
    raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return raw["entities"]


def test_seed_parses_as_valid_json() -> None:
    raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    assert "entities" in raw
    assert isinstance(raw["entities"], list)
    assert len(raw["entities"]) >= 15


def test_every_seed_entry_validates_against_entity_model() -> None:
    for item in _load_seed():
        entity = Entity(**item)
        assert entity.canonical_id
        assert entity.canonical_name


def test_canonical_ids_are_unique() -> None:
    ids = [item["canonical_id"] for item in _load_seed()]
    assert len(ids) == len(set(ids)), "duplicate canonical_id in seed"


def test_canonical_ids_match_pattern() -> None:
    for item in _load_seed():
        assert ID_PATTERN.match(item["canonical_id"]), item["canonical_id"]


def test_hellman_friedman_xi_is_absent() -> None:
    for item in _load_seed():
        name = item["canonical_name"].lower()
        aliases = [a.lower() for a in item.get("aliases", [])]
        is_friedman = "friedman" in name or any("friedman" in a for a in aliases)
        if not is_friedman:
            continue
        # The H&F entity must NOT be the XI fund; X is allowed as the distractor.
        assert " xi" not in name and "_xi" not in item["canonical_id"], (
            f"Hellman & Friedman XI must be absent; found {item['canonical_id']!r}"
        )
        for alias in aliases:
            assert " xi" not in alias, (
                f"Hellman & Friedman XI alias must be absent; found {alias!r}"
            )
