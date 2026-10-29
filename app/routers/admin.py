"""Admin router: cache invalidation and cache warming."""

from fastapi import APIRouter, Depends
from app.cache import TTLCache
from app.dependencies import get_cache, get_meters_service, get_transformers_service
from app.models import RefreshResponse
from app.services.meters import MetersService
from app.services.transformers import TransformersService

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    summary="Force reload cache",
    description="Invalidates cached meter metadata, consumption records, and hierarchy, and forces reload from the portal.",
)
async def refresh_cache(
    cache: TTLCache = Depends(get_cache),
    meters_service: MetersService = Depends(get_meters_service),
    transformers_service: TransformersService = Depends(get_transformers_service),
) -> RefreshResponse:
    """Clear in-memory cache and warm up meter metadata."""
    cleared_entries = await cache.clear()

    # Warm up meter list and transformers
    meters, _ = await meters_service.get_all_meters()
    transformers, _ = await transformers_service.get_all_transformers()

    return RefreshResponse(
        status="Cache successfully invalidated and reloaded",
        meters_count=len(meters),
        dts_count=len(transformers),
        cleared_cache_entries=cleared_entries,
    )

