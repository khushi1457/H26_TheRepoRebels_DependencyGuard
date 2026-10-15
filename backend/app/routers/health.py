"""Basic endpoints that confirm the API is running."""

from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/")
def root() -> dict:
    """Landing endpoint that points users to the interactive docs."""
    return {
        "service": settings.app_name,
        "message": "DependencyGuard API is running. Visit /docs to explore the endpoints.",
    }


@router.get("/health")
def health_check() -> dict:
    """Lightweight check the frontend (or a judge) can call to confirm the backend is up."""
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.version,
    }
