import pytest

from canonical_naming.models import Entity
from canonical_naming.repos.entity_repo import (
    InMemoryEntityRepo,
    load_repo_from_seed,
)


def test_load_repo_from_seed_returns_all_entities() -> None:
    repo = load_repo_from_seed()
    entities = repo.all_entities()
    assert len(entities) >= 15
    assert all(isinstance(e, Entity) for e in entities)


def test_get_returns_entity_by_known_id() -> None:
    repo = load_repo_from_seed()
    entity = repo.get("ent_blackstone_cp_viii")
    assert entity is not None
    assert entity.canonical_name == "Blackstone Capital Partners VIII, L.P."


def test_get_returns_none_for_unknown_id() -> None:
    repo = load_repo_from_seed()
    assert repo.get("ent_does_not_exist") is None


def test_add_inserts_new_entity() -> None:
    repo = InMemoryEntityRepo([])
    entity = Entity(canonical_id="ent_new", canonical_name="New Fund I, L.P.")
    repo.add(entity)
    assert repo.get("ent_new") == entity


def test_add_raises_on_duplicate_id() -> None:
    entity = Entity(canonical_id="ent_dup", canonical_name="Dup Fund")
    repo = InMemoryEntityRepo([entity])
    with pytest.raises(ValueError, match="duplicate canonical_id"):
        repo.add(Entity(canonical_id="ent_dup", canonical_name="Different Name"))


def test_all_entities_returns_full_list() -> None:
    entities = [
        Entity(canonical_id="ent_a", canonical_name="A Fund I, L.P."),
        Entity(canonical_id="ent_b", canonical_name="B Fund II, L.P."),
    ]
    repo = InMemoryEntityRepo(entities)
    result = repo.all_entities()
    assert len(result) == 2
    assert {e.canonical_id for e in result} == {"ent_a", "ent_b"}
