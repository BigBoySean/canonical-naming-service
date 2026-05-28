"""Shared test infrastructure.

Lives in `tests/` rather than `src/` because it is test scaffolding, not
shipped behaviour. `MockLLMMatcher` is the deterministic stand-in that lets
integration tests run fully offline; `SpyMatcher` is a recording stub used
to assert cascade ordering and early-exit behaviour.
"""

from canonical_naming.models import MatchResult, NormalizedName
from canonical_naming.repos.entity_repo import EntityRepo


class MockLLMMatcher:
    """Test double for `LLMMatcher`, satisfying the `Matcher` protocol.

    Construct with a dict mapping `NormalizedName.normalized` (the
    post-pipeline canonical key) to a pre-canned `MatchResult` or `None`.
    Inputs not in the dict return `None`.
    """

    def __init__(self, responses: dict[str, MatchResult | None]) -> None:
        self._responses = responses

    def match(
        self,
        normalized: NormalizedName,
        repo: EntityRepo,
    ) -> MatchResult | None:
        return self._responses.get(normalized.normalized)

    def invalidate_cache(self) -> None:
        """No-op — the mock keeps no index."""
        return None


class SpyMatcher:
    """Recording stub. Returns a pre-set `MatchResult | None` and records
    whether `match()` was ever called. Used to assert cascade order and
    early-exit behaviour in `ResolverService` tests.
    """

    def __init__(self, return_value: MatchResult | None = None) -> None:
        self.return_value = return_value
        self.called = False
        self.invalidated = False

    def match(
        self,
        normalized: NormalizedName,
        repo: EntityRepo,
    ) -> MatchResult | None:
        self.called = True
        return self.return_value

    def invalidate_cache(self) -> None:
        self.invalidated = True
