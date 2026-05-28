from enum import StrEnum

from pydantic import BaseModel, Field


class MatchMethod(StrEnum):
    """Which stage of the cascade produced (or failed to produce) the match."""

    EXACT = "exact"
    FUZZY = "fuzzy"
    LLM = "llm"
    NONE = "none"


class MatchResult(BaseModel):
    """Internal cascade output. Distinct from the API-facing ResolveResponse
    so the HTTP boundary can evolve without leaking internals.
    """

    canonical_id: str | None
    canonical_name: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    method: MatchMethod
    needs_review: bool


class NormalizedName(BaseModel):
    """Output of the normalisation pipeline.

    Downstream matchers compare on `normalized` (the canonical key) and use
    the extracted metadata for finer decisions — for example, a fuzzy match
    that disagrees on `legal_suffix` is weaker than one that agrees, and
    `vehicle_qualifiers` flags feeder/parallel/co-investment variants that
    might warrant a distinct entity ID in v2.
    """

    normalized: str
    legal_suffix: str | None
    vehicle_qualifiers: list[str] = Field(default_factory=list)
    original: str
