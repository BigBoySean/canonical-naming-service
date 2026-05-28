import pytest
from pydantic import ValidationError

from canonical_naming.models import Entity, MatchMethod, MatchResult, ResolveRequest


def test_match_result_rejects_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        MatchResult(
            canonical_id="ent_x",
            canonical_name="X",
            confidence=1.5,
            method=MatchMethod.EXACT,
            needs_review=False,
        )


def test_resolve_request_rejects_empty_raw_name() -> None:
    with pytest.raises(ValidationError):
        ResolveRequest(raw_name="")


def test_entity_is_frozen() -> None:
    entity = Entity(canonical_id="ent_x", canonical_name="X")
    with pytest.raises(ValidationError):
        entity.canonical_name = "Y"


def test_match_method_exact_value() -> None:
    assert MatchMethod.EXACT.value == "exact"
