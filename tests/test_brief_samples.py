"""End-to-end coverage of every brief §3.1 sample input.

One parametrised test, ten cases — the nine resolvable inputs and the
planted `Hellman & Friedman Capital Partners XI` distractor. Each input
goes through the full cascade (`ExactMatcher` + `FuzzyMatcher` +
`MockLLMMatcher({})`) so the LLM tier is *deliberately neutralised*:
this test proves that the deterministic tiers + the seed aliases are
sufficient for every brief sample. The LLM is there to handle inputs
*beyond* the sample set, not to paper over deterministic gaps.

If any of the nine resolvable inputs fails to resolve here, it is a
real finding — either a seed-alias gap or a normaliser edge case — and
gets fixed at the source, not by canning a mock-LLM response.
"""

import pytest

from canonical_naming.matching.exact import ExactMatcher
from canonical_naming.matching.fuzzy import FuzzyMatcher
from canonical_naming.repos.entity_repo import InMemoryEntityRepo, load_repo_from_seed
from canonical_naming.services.resolver import ResolverService
from tests.fixtures import MockLLMMatcher


@pytest.fixture
def service() -> ResolverService:
    repo: InMemoryEntityRepo = load_repo_from_seed()
    return ResolverService(
        matchers=[
            ExactMatcher(),
            FuzzyMatcher(threshold=92),
            MockLLMMatcher({}),  # always declines — forces deterministic tiers
        ],
        repo=repo,
    )


# (raw_name, expected_canonical_id, expected_method, expected_needs_review)
_BRIEF_SAMPLES = [
    ("BCP VIII (USD)",                          "ent_blackstone_cp_viii",   "exact", False),
    ("blackstone cap partners 8",               "ent_blackstone_cp_viii",   "exact", False),
    ("BLACKSTONE CAPITAL PARTNERS VIII LP",     "ent_blackstone_cp_viii",   "exact", False),
    ("KKR Americas Fund 13 - Parallel Vehicle", "ent_kkr_americas_xiii",    "exact", False),
    ("Kohlberg Kravis Roberts Americas XIII",   "ent_kkr_americas_xiii",    "exact", False),
    ("Apollo Inv. Fund IX",                     "ent_apollo_investment_ix", "exact", False),
    ("AIF 9 (Cayman Feeder)",                   "ent_apollo_investment_ix", "exact", False),
    ("CVC Capital Partners Fund VIII SCSp",     "ent_cvc_cp_viii",          "exact", False),
    ("EQT X No 1 SCSp",                         "ent_eqt_x",                "exact", False),
    # The planted distractor: XI is absent from the seed by design.
    ("Hellman & Friedman Capital Partners XI",  None,                       "none",  True),
]


@pytest.mark.parametrize(
    "raw_name,expected_id,expected_method,expected_needs_review",
    _BRIEF_SAMPLES,
    ids=[s[0] for s in _BRIEF_SAMPLES],
)
def test_brief_sample_input_resolves(
    service: ResolverService,
    raw_name: str,
    expected_id: str | None,
    expected_method: str,
    expected_needs_review: bool,
) -> None:
    response = service.resolve(raw_name)

    assert response.raw_name == raw_name
    assert response.canonical_id == expected_id, (
        f"{raw_name!r} resolved to {response.canonical_id!r}, "
        f"expected {expected_id!r}"
    )
    assert response.method.value == expected_method, (
        f"{raw_name!r} resolved via {response.method.value}, "
        f"expected {expected_method}"
    )
    assert response.needs_review is expected_needs_review
