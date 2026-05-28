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
