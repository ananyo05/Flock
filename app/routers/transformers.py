"""Transformers router: list and detail distribution transformers."""

from fastapi import APIRouter, Depends, Query, Response
from app.dependencies import get_transformers_service
from app.models import TransformerSummary, TransformersListResponse
from app.services.transformers import TransformersService

router = APIRouter(prefix="/transformers", tags=["Transformers"])


@router.get(
    "",
    response_model=TransformersListResponse,
    summary="List distribution transformers",
    description="Retrieve all distribution transformers with active connected meter counts.",
)
async def list_transformers(
    response: Response,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    transformers_service: TransformersService = Depends(get_transformers_service),
) -> TransformersListResponse:
    """Return paginated distribution transformer list."""
    result, age = await transformers_service.list_transformers(page=page, page_size=page_size)
    response.headers["X-Cache-Age"] = f"{age:.1f}"
    return result


@router.get(
    "/{code}",
    response_model=TransformerSummary,
    summary="Get transformer by code",
    description="Retrieve single transformer by DT code (e.g., DT-007) with active meter count.",
)
async def get_transformer(
    code: str,
    response: Response,
    transformers_service: TransformersService = Depends(get_transformers_service),
) -> TransformerSummary:
    """Return single transformer summary."""
    result, age = await transformers_service.get_transformer(code=code)
    response.headers["X-Cache-Age"] = f"{age:.1f}"
    return result
