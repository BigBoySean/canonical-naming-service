"""Shared index-building helper for matchers that score against the catalogue.

Both `FuzzyMatcher` and `LLMMatcher` need the same flat list of
`(normalised_key, canonical_id, canonical_name)` tuples — one entry per
canonical name and per alias in the repo. Centralising the build avoids
two near-identical helpers drifting apart as normalisation evolves.

`ExactMatcher` uses a different shape (a dict for O(1) lookup with
collision detection) so it stays on its own builder.
"""

from canonical_naming.matching.normalizer import normalize
from canonical_naming.repos.entity_repo import EntityRepo


def build_normalised_index(repo: EntityRepo) -> list[tuple[str, str, str]]:
    """Return `[(normalised_key, canonical_id, canonical_name), ...]` covering
    every canonical name and every alias in the repo. Duplicates (same key
    appearing on multiple aliases of the same entity) are kept — fuzzy
    scoring is order-independent and dedup by canonical_id happens at the
    consumer (e.g. top-K selection in the LLM tier)."""
    index: list[tuple[str, str, str]] = []
    for entity in repo.all_entities():
        for name in (entity.canonical_name, *entity.aliases):
            key = normalize(name).normalized
            index.append((key, entity.canonical_id, entity.canonical_name))
    return index
