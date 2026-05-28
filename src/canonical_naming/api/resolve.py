"""HTTP boundary for the resolver — `POST /resolve` and `POST /resolve/batch`.

A `needs_review` outcome is always a 200 OK with `needs_review: true` in the
body, not a 4xx. Rationale: `needs_review` is the cascade's deliberate
answer to "is this resolvable?" in the negative case — it's a valid
resolution outcome, not a missing resource or a malformed request. Mapping
it to 404 would conflate "the input could not be resolved" with "the
request was rejected", which are categorically different conditions.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from canonical_naming.api.deps import get_resolver_service
from canonical_naming.models import (
    BatchResolveRequest,
    BatchResolveResponse,
    ResolveRequest,
    ResolveResponse,
)
from canonical_naming.services.resolver import ResolverService

router = APIRouter(tags=["resolve"])


@router.post(
    "/resolve",
    response_model=ResolveResponse,
    summary="Resolve a raw fund/partnership name to a canonical entity",
)
def resolve_one(
    request: ResolveRequest,
    service: Annotated[ResolverService, Depends(get_resolver_service)],
) -> ResolveResponse:
    return service.resolve(request.raw_name)


@router.post(
    "/resolve/batch",
    response_model=BatchResolveResponse,
    summary="Resolve a list of raw names in one call",
)
def resolve_batch(
    request: BatchResolveRequest,
    service: Annotated[ResolverService, Depends(get_resolver_service)],
) -> BatchResolveResponse:
    results = service.resolve_batch(request.raw_names)
    return BatchResolveResponse(results=results)
