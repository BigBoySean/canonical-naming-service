from fastapi import FastAPI

from canonical_naming import __version__
from canonical_naming.api.entities import router as entities_router
from canonical_naming.api.health import router as health_router
from canonical_naming.api.resolve import router as resolve_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Canonical Naming Service",
        version=__version__,
        description=(
            "Resolves raw PE fund/partnership names to canonical entities "
            "via a tiered matching cascade."
        ),
    )
    app.include_router(health_router)
    app.include_router(resolve_router)
    app.include_router(entities_router)
    return app


app = create_app()
