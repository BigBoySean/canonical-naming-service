from pydantic import BaseModel, Field

from canonical_naming.models.matching import MatchMethod


class ResolveRequest(BaseModel):
    raw_name: str = Field(min_length=1)


class ResolveResponse(BaseModel):
    raw_name: str
    canonical_id: str | None
    canonical_name: str | None
    confidence: float
    method: MatchMethod
    needs_review: bool


class BatchResolveRequest(BaseModel):
    raw_names: list[str] = Field(min_length=1)


class BatchResolveResponse(BaseModel):
    results: list[ResolveResponse]


class CreateEntityRequest(BaseModel):
    canonical_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
