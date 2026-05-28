import json
from pathlib import Path
from typing import Protocol

from canonical_naming.models import Entity


class EntityRepo(Protocol):
    """Storage protocol for canonical entities.

    The in-memory implementation in this module is sufficient for the
    assessment. Production swaps this for a Postgres-backed implementation
    (pg_trgm for fuzzy candidate retrieval, tsvector for full-text) — the
    interface stays identical, so nothing in the matching cascade, the
    services, or the API has to change.
    """

    def get(self, canonical_id: str) -> Entity | None: ...
    def add(self, entity: Entity) -> None: ...
    def all_entities(self) -> list[Entity]: ...


class InMemoryEntityRepo:
    """In-memory `EntityRepo`. Keyed by canonical_id; rejects duplicate ids."""

    def __init__(self, entities: list[Entity]) -> None:
        self._entities: dict[str, Entity] = {}
        for entity in entities:
            self.add(entity)

    def get(self, canonical_id: str) -> Entity | None:
        return self._entities.get(canonical_id)

    def add(self, entity: Entity) -> None:
        if entity.canonical_id in self._entities:
            raise ValueError(f"duplicate canonical_id: {entity.canonical_id!r}")
        self._entities[entity.canonical_id] = entity

    def all_entities(self) -> list[Entity]:
        return list(self._entities.values())


def _default_seed_path() -> Path:
    """Locate `data/seed.json` from the installed package, robust to cwd.

    Walks up from this module's file location looking for a sibling
    `data/seed.json`. Works for editable installs (`pip install -e .`) where
    the source layout is preserved on disk.
    """
    here = Path(__file__).resolve().parent
    for ancestor in (here, *here.parents):
        candidate = ancestor / "data" / "seed.json"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "could not locate data/seed.json from package root"
    )


def load_repo_from_seed(path: Path | None = None) -> InMemoryEntityRepo:
    """Load `data/seed.json` and return a populated InMemoryEntityRepo.

    Each entry is validated against the `Entity` model on load — malformed
    seed data fails fast at startup, not mid-request.
    """
    seed_path = path if path is not None else _default_seed_path()
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    entities = [Entity(**item) for item in raw["entities"]]
    return InMemoryEntityRepo(entities)
