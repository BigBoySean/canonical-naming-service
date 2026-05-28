import pytest

from canonical_naming.matching.exact import ExactMatcher
from canonical_naming.matching.normalizer import normalize
from canonical_naming.models import MatchMethod
from canonical_naming.repos.entity_repo import InMemoryEntityRepo, load_repo_from_seed


@pytest.fixture
def repo() -> InMemoryEntityRepo:
    return load_repo_from_seed()


@pytest.fixture
def matcher() -> ExactMatcher:
    return ExactMatcher()


def test_bcp_viii_hits_blackstone_via_alias(
    matcher: ExactMatcher, repo: InMemoryEntityRepo
) -> None:
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is not None
    assert result.canonical_id == "ent_blackstone_cp_viii"
    assert result.method == MatchMethod.EXACT
    assert result.confidence == 1.0
    assert result.needs_review is False


def test_full_canonical_name_hits_blackstone(
    matcher: ExactMatcher, repo: InMemoryEntityRepo
) -> None:
    # Brief sample input — should hit via canonical name (after suffix strip
    # + Roman conversion both sides normalise identically).
    result = matcher.match(
        normalize("BLACKSTONE CAPITAL PARTNERS VIII LP"), repo
    )
    assert result is not None
    assert result.canonical_id == "ent_blackstone_cp_viii"
    assert result.method == MatchMethod.EXACT
    assert result.confidence == 1.0


def test_cvc_brief_sample_hits(matcher: ExactMatcher, repo: InMemoryEntityRepo) -> None:
    result = matcher.match(normalize("CVC Capital Partners Fund VIII SCSp"), repo)
    assert result is not None
    assert result.canonical_id == "ent_cvc_cp_viii"


def test_kkr_brief_sample_hits(matcher: ExactMatcher, repo: InMemoryEntityRepo) -> None:
    result = matcher.match(
        normalize("Kohlberg Kravis Roberts Americas XIII"), repo
    )
    assert result is not None
    assert result.canonical_id == "ent_kkr_americas_xiii"


def test_unknown_name_returns_none(
    matcher: ExactMatcher, repo: InMemoryEntityRepo
) -> None:
    result = matcher.match(normalize("Completely Made Up Fund ZZZ"), repo)
    assert result is None


def test_empty_repo_returns_none(matcher: ExactMatcher) -> None:
    repo = InMemoryEntityRepo([])
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is None
