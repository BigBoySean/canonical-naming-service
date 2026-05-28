"""Cascade tier interface.

The `Matcher` protocol is the seam between the resolver service and the
individual matching strategies (exact, fuzzy, LLM, ...). Implementations
return a `MatchResult` when they resolve confidently, or `None` to pass
the input through to the next tier.

Why `None` rather than a low-confidence `MatchResult`: the cascade should
not have to know what "low confidence" means for each tier — that's a
per-matcher concept tied to the matcher's internal scoring semantics. By
making the contract binary (`MatchResult` or `None`), the cascade stays
trivially simple ("first non-None wins") and each matcher remains free to
tune its own threshold and decision logic.
"""

from typing import Protocol, runtime_checkable

from canonical_naming.models import MatchResult, NormalizedName
from canonical_naming.repos.entity_repo import EntityRepo


@runtime_checkable
class Matcher(Protocol):
    """A single tier in the resolution cascade.

    Implementations: `ExactMatcher`, `FuzzyMatcher`, `LLMMatcher`.
    The resolver service calls them in order and accepts the first
    non-None result; if every matcher returns `None`, the service
    synthesises a `needs_review` response.

    `invalidate_cache()` is part of the contract because every matcher
    here builds an index over the repo's entities and caches it. After
    a repo mutation (`POST /entities` -> `repo.add()`), the resolver
    service calls `invalidate_cache()` on each matcher so the next
    `match()` rebuilds against the new state. Matchers without internal
    caching implement it as a no-op (e.g. the test mock).
    """

    def match(
        self,
        normalized: NormalizedName,
        repo: EntityRepo,
    ) -> MatchResult | None: ...

    def invalidate_cache(self) -> None: ...
