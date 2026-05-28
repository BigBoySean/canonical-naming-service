"""FastAPI dependency wiring.

`get_resolver_service()` builds the production cascade once and returns
the same instance for every request — the `lru_cache` makes it a
process-scoped singleton. The repo lives inside the service, so
`POST /entities` mutations persist across requests within a single
process (until restart), and matcher caches are invalidated on each
mutation per the staleness fix in `ResolverService.register_entity()`.

Tests override this dependency via `app.dependency_overrides` to inject
a `ResolverService` built with `MockLLMMatcher` so the suite runs offline.
"""

from functools import lru_cache

from canonical_naming.config import get_settings
from canonical_naming.matching.exact import ExactMatcher
from canonical_naming.matching.fuzzy import FuzzyMatcher
from canonical_naming.matching.llm import LLMMatcher
from canonical_naming.repos.entity_repo import load_repo_from_seed
from canonical_naming.services.resolver import ResolverService


@lru_cache(maxsize=1)
def get_resolver_service() -> ResolverService:
    """Process-singleton resolver service.

    The LLM tier respects `settings.llm_enabled`; with the default
    `LLM_ENABLED=false`, the cascade runs 3-stage and the LLMMatcher
    returns None without constructing an Anthropic client.
    """
    settings = get_settings()
    repo = load_repo_from_seed()
    matchers = [
        ExactMatcher(),
        FuzzyMatcher(threshold=settings.fuzzy_threshold),
        LLMMatcher(settings=settings),
    ]
    return ResolverService(matchers=matchers, repo=repo)
