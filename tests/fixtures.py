"""Shared test infrastructure.

Lives in `tests/` rather than `src/` because it is test scaffolding, not
shipped behaviour. The `MockLLMMatcher` here is the deterministic stand-in
that lets the resolver-service integration tests run fully offline (no
Anthropic SDK call, no API key, no network).
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
