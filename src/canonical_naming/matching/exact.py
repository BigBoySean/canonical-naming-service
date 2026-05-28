"""Exact-match tier — O(1) lookup against normalised canonical names and aliases.

The matcher builds an index that maps `normalize(name).normalized` to
`(canonical_id, canonical_name)` for every canonical name and every alias
in the repo. Collisions across *different* canonical IDs are treated as
ambiguous (logged at WARNING and dropped from the index), because an
ambiguous exact match isn't an exact match — better to fall through to
fuzzy / LLM than to pick one arbitrarily.

Index strategy: lazy-built per `id(repo)`, cached on the matcher instance.
The in-memory seed has ~20 entities and ~80 aliases; rebuilding from
scratch is microseconds, but caching by repo identity means long-lived
matcher instances do that work exactly once per repo they see.
"""

import logging

from canonical_naming.matching.normalizer import normalize
from canonical_naming.models import MatchMethod, MatchResult, NormalizedName
from canonical_naming.repos.entity_repo import EntityRepo

logger = logging.getLogger(__name__)


class ExactMatcher:
    """Exact-match tier (cascade stage 2)."""

    def __init__(self) -> None:
        # Cache keyed on id(repo). Long-lived matchers see the same repo
        # repeatedly and avoid rebuilding the index every call.
        self._index_cache: dict[int, dict[str, tuple[str, str]]] = {}

    def _build_index(self, repo: EntityRepo) -> dict[str, tuple[str, str]]:
        index: dict[str, tuple[str, str]] = {}
        collided: set[str] = set()
        for entity in repo.all_entities():
            for name in (entity.canonical_name, *entity.aliases):
                key = normalize(name).normalized
                if key in collided:
                    continue
                existing = index.get(key)
                if existing is not None and existing[0] != entity.canonical_id:
                    logger.warning(
                        "exact-match collision on %r: %s vs %s "
                        "— treating as ambiguous (miss)",
                        key, existing[0], entity.canonical_id,
                    )
                    collided.add(key)
                    del index[key]
                    continue
                index[key] = (entity.canonical_id, entity.canonical_name)
        return index

    def _get_index(self, repo: EntityRepo) -> dict[str, tuple[str, str]]:
        rid = id(repo)
        cached = self._index_cache.get(rid)
        if cached is not None:
            return cached
        index = self._build_index(repo)
        self._index_cache[rid] = index
        return index

    def invalidate_cache(self) -> None:
        self._index_cache.clear()

    def match(
        self,
        normalized: NormalizedName,
        repo: EntityRepo,
    ) -> MatchResult | None:
        hit = self._get_index(repo).get(normalized.normalized)
        if hit is None:
            return None
        canonical_id, canonical_name = hit
        return MatchResult(
            canonical_id=canonical_id,
            canonical_name=canonical_name,
            confidence=1.0,
            method=MatchMethod.EXACT,
            needs_review=False,
        )
