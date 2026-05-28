"""HTTP boundary for entity catalogue reads/writes.

`GET /entities/{id}` — 200 with the entity or 404 if absent. 404 here is
the right status: an unknown id is a missing resource.

`POST /entities` — 201 Created when the entity is registered; 409 Conflict
if the slugified id collides with an existing entity (see
`ResolverService.register_entity` for the strict-collision rationale).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from canonical_naming.api.deps import get_resolver_service
from canonical_naming.models import CreateEntityRequest, Entity
from canonical_naming.services.resolver import ResolverService

router = APIRouter(tags=["entities"])


@router.get(
    "/entities/{entity_id}",
    response_model=Entity,
    summary="Retrieve a canonical entity by id",
    responses={404: {"description": "entity not found"}},
)
def get_entity(
    entity_id: str,
    service: Annotated[ResolverService, Depends(get_resolver_service)],
) -> Entity:
    entity = service.get_entity(entity_id)
    if entity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"entity {entity_id!r} not found",
        )
    return entity


@router.post(
    "/entities",
    response_model=Entity,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new canonical entity",
    responses={409: {"description": "id collision with an existing entity"}},
)
def create_entity(
    request: CreateEntityRequest,
    service: Annotated[ResolverService, Depends(get_resolver_service)],
) -> Entity:
    try:
        return service.register_entity(
            canonical_name=request.canonical_name,
            aliases=request.aliases,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
