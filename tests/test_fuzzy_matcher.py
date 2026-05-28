import logging

import pytest

from canonical_naming.matching.fuzzy import FuzzyMatcher, _extract_fund_numeral
from canonical_naming.matching.normalizer import normalize
from canonical_naming.models import Entity, MatchMethod
from canonical_naming.repos.entity_repo import InMemoryEntityRepo, load_repo_from_seed


@pytest.fixture
def repo() -> InMemoryEntityRepo:
    return load_repo_from_seed()


@pytest.fixture
def matcher() -> FuzzyMatcher:
    return FuzzyMatcher(threshold=92)


# ============================================================================
# HEADLINE TEST — brief's planted H&F XI distractor.
# ============================================================================
# Without the numeral guard, `Hellman & Friedman Capital Partners XI`
# normalises to `... 11`, scores ~97 on token_set_ratio against H&F X
# (`... 10`) — every token shared except the vintage — and would emit a
# false confident fuzzy match. The guard refuses, the cascade falls
# through to LLM / needs_review. This is the brief's intended behaviour
# for the planted case.
# ============================================================================


def test_hf_xi_must_not_false_match_to_hf_x(
    matcher: FuzzyMatcher,
    repo: InMemoryEntityRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="canonical_naming.matching.fuzzy")
    result = matcher.match(
        normalize("Hellman & Friedman Capital Partners XI"), repo
    )
    assert result is None, (
        "numeral guard must block the XI -> X false match — "
        "this is the brief's planted needs_review case"
    )
    # Confirm the guard actually fired (rather than the score happening to
    # fall below threshold for some unrelated reason).
    assert "numeral guard" in caplog.text.lower(), (
        "expected numeral-guard log line at INFO"
    )


# ============================================================================
# Positive fuzzy matches (genuine near-misses, not exact-aliasable)
# ============================================================================


def test_typoed_carlyle_partnes_matches(
    matcher: FuzzyMatcher, repo: InMemoryEntityRepo
) -> None:
    # "Partnes" missing the 'r' — fuzzy handles single-char typos.
    # Note: confidence can hit 1.0 even via the fuzzy tier because
    # `token_set_ratio` returns 100 when one side's tokens are a strict
    # subset of the other's — the Carlyle entity has the alias
    # `Carlyle VIII` (normalised `carlyle 8`), which is a subset of the
    # input's normalised form `carlyle partnes 8`. That's still a fuzzy
    # match (not exact, because the input didn't key-hit the index), just
    # a maximally-confident one.
    result = matcher.match(normalize("Carlyle Partnes VIII"), repo)
    assert result is not None
    assert result.canonical_id == "ent_carlyle_partners_viii"
    assert result.method == MatchMethod.FUZZY
    assert result.confidence >= 0.92


def test_typoed_blackstone_matches(
    matcher: FuzzyMatcher, repo: InMemoryEntityRepo
) -> None:
    # "Blacstone" missing the 'k' — typo in the GP name, fund numeral intact.
    result = matcher.match(
        normalize("Blacstone Capital Partners VIII"), repo
    )
    assert result is not None
    assert result.canonical_id == "ent_blackstone_cp_viii"
    assert result.method == MatchMethod.FUZZY


# ============================================================================
# Below threshold → None
# ============================================================================


def test_garbage_input_returns_none(
    matcher: FuzzyMatcher, repo: InMemoryEntityRepo
) -> None:
    result = matcher.match(normalize("xyzzy plugh fund"), repo)
    assert result is None


# ============================================================================
# Numeral guard — explicit unit tests on a synthetic repo
# ============================================================================


def _single_entity_repo(canonical_id: str, canonical_name: str) -> InMemoryEntityRepo:
    return InMemoryEntityRepo([
        Entity(canonical_id=canonical_id, canonical_name=canonical_name)
    ])


def test_numeral_guard_same_numeral_allows_match(matcher: FuzzyMatcher) -> None:
    repo = _single_entity_repo(
        "ent_test", "Test Capital Partners VIII, L.P."
    )
    # Singular "Partner" → near-miss, same fund numeral. Guard doesn't fire.
    result = matcher.match(normalize("Test Capital Partner VIII"), repo)
    assert result is not None
    assert result.canonical_id == "ent_test"


def test_numeral_guard_different_numeral_blocks_match(
    matcher: FuzzyMatcher,
) -> None:
    repo = _single_entity_repo(
        "ent_test", "Test Capital Partners VIII, L.P."
    )
    # Same name, IX (=9) vs VIII (=8). High string score; guard blocks.
    result = matcher.match(normalize("Test Capital Partners IX"), repo)
    assert result is None


def test_numeral_guard_does_not_block_when_input_has_no_numeral(
    matcher: FuzzyMatcher,
) -> None:
    repo = _single_entity_repo(
        "ent_test", "Test Capital Partners VIII, L.P."
    )
    # Input has no numeral — guard cannot fire. Match by string score alone.
    result = matcher.match(normalize("Test Capital Partners"), repo)
    assert result is not None
    assert result.canonical_id == "ent_test"


def test_numeral_guard_does_not_block_when_candidate_has_no_numeral(
    matcher: FuzzyMatcher,
) -> None:
    repo = _single_entity_repo("ent_holdings", "Holdings Group")
    # Candidate has no numeral — guard cannot fire. Match by string score alone.
    result = matcher.match(normalize("Holdings Group VIII"), repo)
    assert result is not None
    assert result.canonical_id == "ent_holdings"


# ============================================================================
# Fund numeral extraction (unit test of the helper)
# ============================================================================


def test_fund_numeral_extracts_simple_trailing_digit() -> None:
    assert _extract_fund_numeral("blackstone capital partners 8") == 8


def test_fund_numeral_extracts_two_digit() -> None:
    assert _extract_fund_numeral("hellman and friedman capital partners 11") == 11


def test_fund_numeral_skips_no_n_series_marker() -> None:
    # `no 1` is series, NOT the fund numeral. Fund numeral is the 10.
    assert _extract_fund_numeral("eqt 10 no 1") == 10


def test_fund_numeral_returns_none_when_no_digits() -> None:
    assert _extract_fund_numeral("apollo") is None


def test_fund_numeral_first_match_wins() -> None:
    assert _extract_fund_numeral("foo 5 bar 7") == 5


def test_fund_numeral_returns_none_when_only_series_marker_present() -> None:
    # Pathological: only a series marker, no fund numeral.
    assert _extract_fund_numeral("no 1") is None


def test_fund_numeral_handles_empty_string() -> None:
    assert _extract_fund_numeral("") is None
