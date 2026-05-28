"""Tests for `ResolverService` — the 4-tier cascade orchestrator."""

import re

import pytest

from canonical_naming.matching.base import Matcher
from canonical_naming.matching.exact import ExactMatcher
from canonical_naming.matching.fuzzy import FuzzyMatcher
from canonical_naming.models import MatchMethod, MatchResult
from canonical_naming.repos.entity_repo import InMemoryEntityRepo, load_repo_from_seed
from canonical_naming.services.resolver import ResolverService, _slugify
from tests.fixtures import MockLLMMatcher, SpyMatcher


@pytest.fixture
def repo() -> InMemoryEntityRepo:
    return load_repo_from_seed()


def _real_cascade(repo: InMemoryEntityRepo) -> ResolverService:
    """Production-shaped cascade: exact -> fuzzy -> mock-LLM. Mock returns
    None for everything so the cascade exits cleanly at needs_review when
    the deterministic tiers don't decide."""
    return ResolverService(
        matchers=[ExactMatcher(), FuzzyMatcher(threshold=92), MockLLMMatcher({})],
        repo=repo,
    )


# ============================================================================
# SpyMatcher and MockLLMMatcher satisfy the Matcher protocol
# ============================================================================


def test_spy_matcher_satisfies_protocol() -> None:
    spy = SpyMatcher()
    assert isinstance(spy, Matcher)


def test_mock_llm_matcher_satisfies_protocol_after_invalidate_added() -> None:
    mock = MockLLMMatcher({})
    assert isinstance(mock, Matcher)


# ============================================================================
# Cascade order + early exit
# ============================================================================


def test_first_matcher_hit_short_circuits_downstream(
    repo: InMemoryEntityRepo,
) -> None:
    hit = MatchResult(
        canonical_id="ent_blackstone_cp_viii",
        canonical_name="Blackstone Capital Partners VIII, L.P.",
        confidence=1.0,
        method=MatchMethod.EXACT,
        needs_review=False,
    )
    exact = SpyMatcher(return_value=hit)
    fuzzy = SpyMatcher(return_value=None)
    llm = SpyMatcher(return_value=None)
    service = ResolverService(matchers=[exact, fuzzy, llm], repo=repo)

    result = service.resolve("BCP VIII")

    assert exact.called is True
    assert fuzzy.called is False, "fuzzy must not be called after exact hits"
    assert llm.called is False, "LLM must not be called after exact hits"
    assert result.canonical_id == "ent_blackstone_cp_viii"
    assert result.method == MatchMethod.EXACT
    assert result.needs_review is False


def test_fuzzy_tier_reached_when_exact_misses(repo: InMemoryEntityRepo) -> None:
    fuzzy_hit = MatchResult(
        canonical_id="ent_carlyle_partners_viii",
        canonical_name="Carlyle Partners VIII, L.P.",
        confidence=0.95,
        method=MatchMethod.FUZZY,
        needs_review=False,
    )
    exact = SpyMatcher(return_value=None)
    fuzzy = SpyMatcher(return_value=fuzzy_hit)
    llm = SpyMatcher(return_value=None)
    service = ResolverService(matchers=[exact, fuzzy, llm], repo=repo)

    result = service.resolve("Carlyle Partnes VIII")

    assert exact.called is True
    assert fuzzy.called is True
    assert llm.called is False
    assert result.method == MatchMethod.FUZZY


def test_llm_tier_reached_when_exact_and_fuzzy_miss(
    repo: InMemoryEntityRepo,
) -> None:
    llm_hit = MatchResult(
        canonical_id="ent_eqt_x",
        canonical_name="EQT X, SCSp",
        confidence=0.92,
        method=MatchMethod.LLM,
        needs_review=False,
    )
    exact = SpyMatcher(return_value=None)
    fuzzy = SpyMatcher(return_value=None)
    llm = SpyMatcher(return_value=llm_hit)
    service = ResolverService(matchers=[exact, fuzzy, llm], repo=repo)

    result = service.resolve("EQT 10 (Luxembourg)")

    assert exact.called is True
    assert fuzzy.called is True
    assert llm.called is True
    assert result.method == MatchMethod.LLM


# ============================================================================
# needs_review fallback
# ============================================================================


def test_all_matchers_decline_returns_needs_review(
    repo: InMemoryEntityRepo,
) -> None:
    service = ResolverService(
        matchers=[
            SpyMatcher(return_value=None),
            SpyMatcher(return_value=None),
            SpyMatcher(return_value=None),
        ],
        repo=repo,
    )
    result = service.resolve("Completely Unknown Fund ZZZ")

    assert result.canonical_id is None
    assert result.canonical_name is None
    assert result.method == MatchMethod.NONE
    assert result.needs_review is True
    assert result.confidence == 0.0
    assert result.raw_name == "Completely Unknown Fund ZZZ"


def test_unknown_input_through_real_cascade_returns_needs_review(
    repo: InMemoryEntityRepo,
) -> None:
    service = _real_cascade(repo)
    result = service.resolve("Completely Made Up Fund ZZZ")
    assert result.needs_review is True
    assert result.method == MatchMethod.NONE


# ============================================================================
# H&F XI end-to-end — the brief's planted distractor through the full cascade
# ============================================================================


def test_hf_xi_falls_through_full_cascade_to_needs_review(
    repo: InMemoryEntityRepo,
) -> None:
    """The headline integration test.

    `Hellman & Friedman Capital Partners XI` is absent from the seed by
    design. Exact misses (normalised key not in index). Fuzzy scores ~97
    against H&F X but the numeral guard refuses (11 vs 10). The mock LLM
    returns None (offline). End state: needs_review with method NONE.
    """
    service = _real_cascade(repo)
    result = service.resolve("Hellman & Friedman Capital Partners XI")

    assert result.needs_review is True
    assert result.canonical_id is None
    assert result.method == MatchMethod.NONE
    assert result.raw_name == "Hellman & Friedman Capital Partners XI"


# ============================================================================
# resolve_batch
# ============================================================================


def test_resolve_batch_returns_responses_in_input_order(
    repo: InMemoryEntityRepo,
) -> None:
    service = _real_cascade(repo)
    inputs = [
        "BCP VIII",
        "Apollo Inv. Fund IX",
        "Completely Made Up Fund ZZZ",
        "KKR Americas Fund 13 - Parallel Vehicle",
    ]
    results = service.resolve_batch(inputs)

    assert len(results) == 4
    assert results[0].canonical_id == "ent_blackstone_cp_viii"
    assert results[1].canonical_id == "ent_apollo_investment_ix"
    assert results[2].needs_review is True
    assert results[3].canonical_id == "ent_kkr_americas_xiii"
    # Each response echoes its own raw_name.
    for response, raw in zip(results, inputs, strict=True):
        assert response.raw_name == raw


def test_resolve_batch_empty_returns_empty_list(repo: InMemoryEntityRepo) -> None:
    service = _real_cascade(repo)
    assert service.resolve_batch([]) == []


# ============================================================================
# register_entity — slug, collision, and the cache-invalidation test
# ============================================================================


def test_register_entity_generates_ent_slug_pattern(
    repo: InMemoryEntityRepo,
) -> None:
    service = _real_cascade(repo)
    entity = service.register_entity(
        canonical_name="Brand New Fund III, L.P.",
        aliases=["Brand New III"],
    )
    assert re.match(r"^ent_[a-z0-9_]+$", entity.canonical_id), entity.canonical_id
    assert "brand" in entity.canonical_id
    assert "new" in entity.canonical_id
    assert entity.canonical_name == "Brand New Fund III, L.P."
    assert entity.aliases == ["Brand New III"]


def test_register_entity_then_resolve_finds_it(
    repo: InMemoryEntityRepo,
) -> None:
    """The cache-invalidation test.

    Each matcher caches its normalised index keyed on `id(repo)`. Without
    `invalidate_cache()` called on every matcher after `register_entity`,
    the cached index from the pre-registration state would not include
    the new entity, and `resolve()` would miss it.
    """
    service = _real_cascade(repo)

    # Step 1: do an initial resolve to seed every matcher's cache.
    pre = service.resolve("BCP VIII")
    assert pre.canonical_id == "ent_blackstone_cp_viii"

    # Step 2: register a brand-new entity.
    new_entity = service.register_entity(
        canonical_name="Brand New Fund III, L.P.",
        aliases=["Brand New III", "BNF III"],
    )

    # Step 3: resolve a name that should hit the new entity. If invalidation
    # is broken, this lands in needs_review instead.
    result = service.resolve("Brand New Fund III, L.P.")
    assert result.canonical_id == new_entity.canonical_id, (
        "cache invalidation failed: new entity not visible to ExactMatcher"
    )
    assert result.method == MatchMethod.EXACT
    assert result.needs_review is False


def test_register_entity_raises_on_id_collision(
    repo: InMemoryEntityRepo,
) -> None:
    service = _real_cascade(repo)
    # Use a name whose slug matches an existing seed canonical_id.
    # `ent_blackstone_cp_viii` is the seed id; "blackstone cp viii" slugifies
    # to the same shape, but the seed's canonical_name is
    # `Blackstone Capital Partners VIII, L.P.` which slugifies to
    # `ent_blackstone_capital_partners_viii_l_p`. We need to engineer a
    # collision: register the same canonical_name twice.
    service.register_entity(canonical_name="Duplicate Fund I, L.P.")
    with pytest.raises(ValueError, match="canonical_id collision"):
        service.register_entity(canonical_name="Duplicate Fund I, L.P.")


def test_register_entity_invalidates_every_matcher_cache(
    repo: InMemoryEntityRepo,
) -> None:
    matchers = [SpyMatcher(), SpyMatcher(), SpyMatcher()]
    service = ResolverService(matchers=matchers, repo=repo)
    service.register_entity(canonical_name="Cache Check Fund I, L.P.")
    for m in matchers:
        assert m.invalidated is True, "every matcher must be invalidated"


# ============================================================================
# _slugify unit tests
# ============================================================================


def test_slugify_simple_name() -> None:
    assert _slugify("Brand New Fund III") == "ent_brand_new_fund_iii"


def test_slugify_folds_diacritics() -> None:
    assert _slugify("Société Générale Partners II") == "ent_societe_generale_partners_ii"


def test_slugify_strips_punctuation() -> None:
    assert _slugify("Foo Fund VIII, L.P.") == "ent_foo_fund_viii_l_p"


def test_slugify_handles_ampersand() -> None:
    assert _slugify("Hellman & Friedman X") == "ent_hellman_friedman_x"


def test_slugify_empty_raises() -> None:
    with pytest.raises(ValueError, match="cannot derive slug"):
        _slugify("!!!")
