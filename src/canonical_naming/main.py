from fastapi import FastAPI

from canonical_naming import __version__
from canonical_naming.api.health import router as health_router


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
    return app


app = create_app()
