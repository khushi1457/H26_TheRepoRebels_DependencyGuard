"""Entry point: builds the FastAPI application, registers routers and serves the web UI."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.config import settings
from app.routers import analysis, health

STATIC = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.version, description=settings.description)
    app.include_router(health.router)
    app.include_router(analysis.router)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
