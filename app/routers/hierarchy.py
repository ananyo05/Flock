"""Hierarchy router: distribution network tree and data quality auditing."""

from fastapi import APIRouter, Depends, Response
from app.dependencies import get_hierarchy_service
from app.models import HierarchyTreeResponse
from app.services.hierarchy import HierarchyService

router = APIRouter(prefix="/hierarchy", tags=["Hierarchy"])


@router.get(
    "",
    response_model=HierarchyTreeResponse,
    summary="Get electrical distribution hierarchy tree",
    description="Returns full tree from Zone down to Distribution Transformer (DT) with meter counts per node and audited data quality inconsistencies.",
)
async def get_hierarchy(
    response: Response,
    hierarchy_service: HierarchyService = Depends(get_hierarchy_service),
) -> HierarchyTreeResponse:
    """Return distribution hierarchy tree and data quality anomalies."""
    result, age = await hierarchy_service.get_hierarchy_tree()
    response.headers["X-Cache-Age"] = f"{age:.1f}"
    return result
