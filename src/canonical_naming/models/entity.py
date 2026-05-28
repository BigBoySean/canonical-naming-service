from pydantic import BaseModel, ConfigDict, Field


class Entity(BaseModel):
    """A canonical PE partnership / fund entity.

    Reference data: frozen after construction. Mutations happen by
    re-registering through the repo, not by editing in place.
    """

    model_config = ConfigDict(frozen=True)

    canonical_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
