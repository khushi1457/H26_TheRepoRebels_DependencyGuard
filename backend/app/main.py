"""Entry point: builds the FastAPI application and registers all routers."""

from fastapi import FastAPI

from app.config import settings
from app.routers import health


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=settings.description,
    )
    app.include_router(health.router)
    return app


app = create_app()
