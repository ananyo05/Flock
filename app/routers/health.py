"""Health check router."""

from fastapi import APIRouter
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field("ok", description="Service health status", examples=["ok"])


router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    description="Returns service status to verify the API server is operational.",
)
async def health_check() -> HealthResponse:
    """Return healthy status."""
    return HealthResponse(status="ok")
