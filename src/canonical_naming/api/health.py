from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from canonical_naming import __version__

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
