"""Meters router: list, search, details, consumption, and nearby spatial queries."""

from fastapi import APIRouter, Depends, Query, Response
from app.dependencies import get_consumption_service, get_meters_service
from app.models import (
    ConsumptionResponse,
    MeterDetailResponse,
    NearbyMetersResponse,
    PaginatedMetersResponse,
)
from app.services.consumption import ConsumptionService
from app.services.meters import MetersService

router = APIRouter(prefix="/meters", tags=["Meters"])


@router.get(
    "",
    response_model=PaginatedMetersResponse,
    summary="List and search meters",
    description="Retrieve a paginated list of meters with optional filtering by query, make, status, phase, and DT.",
)
async def list_meters(
    response: Response,
    q: str | None = Query(None, description="Substring search on meter ID or serial number"),
    make: str | None = Query(None, description="Filter by meter manufacturer (e.g., HPL, Genus, L&T, Allied, Secure)"),
    status: str | None = Query(None, description="Filter by installation status (Installed, Faulty, Decommissioned)"),
    phase: str | None = Query(None, description="Filter by phase configuration (single, three)"),
    dt: str | None = Query(None, description="Filter by distribution transformer code (e.g., DT-001)"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    meters_service: MetersService = Depends(get_meters_service),
) -> PaginatedMetersResponse:
    """Return paginated meter summary results."""
    result, age = await meters_service.list_meters(
        q=q,
        make=make,
        status=status,
        phase=phase,
        dt=dt,
        page=page,
        page_size=page_size,
    )
    response.headers["X-Cache-Age"] = f"{age:.1f}"
    return result


@router.get(
    "/nearby",
    response_model=NearbyMetersResponse,
    summary="Find nearby meters",
    description="Locate meters within a given radius in kilometers using bounding-box prefiltering and Haversine distance.",
)
async def get_nearby_meters(
    response: Response,
    lat: float = Query(..., ge=-90.0, le=90.0, description="Center latitude in decimal degrees"),
    lon: float = Query(..., ge=-180.0, le=180.0, description="Center longitude in decimal degrees"),
    radius_km: float = Query(5.0, gt=0.0, le=500.0, description="Search radius in kilometers"),
    meters_service: MetersService = Depends(get_meters_service),
) -> NearbyMetersResponse:
    """Search meters by geographic proximity."""
    result, age = await meters_service.get_nearby_meters(
        lat=lat,
        lon=lon,
        radius_km=radius_km,
    )
    response.headers["X-Cache-Age"] = f"{age:.1f}"
    return result


@router.get(
    "/{meter_id}",
    response_model=MeterDetailResponse,
    summary="Get meter detail",
    description="Retrieve comprehensive details for a specific meter including installation type, location coordinates, and full distribution hierarchy.",
)
async def get_meter_detail(
    meter_id: str,
    response: Response,
    meters_service: MetersService = Depends(get_meters_service),
) -> MeterDetailResponse:
    """Return single meter detail."""
    result, age = await meters_service.get_meter_detail(meter_id=meter_id)
    response.headers["X-Cache-Age"] = f"{age:.1f}"
    return result


@router.get(
    "/{meter_id}/consumption",
    response_model=ConsumptionResponse,
    summary="Get meter consumption time-series",
    description="Fetch energy register readings with ISO-8601 timestamps (+05:30), computed interval deltas, inferred interval, and anomaly flags.",
)
async def get_meter_consumption(
    meter_id: str,
    response: Response,
    from_date: str | None = Query(None, alias="from", description="Start date in YYYY-MM-DD format"),
    to_date: str | None = Query(None, alias="to", description="End date in YYYY-MM-DD format"),
    consumption_service: ConsumptionService = Depends(get_consumption_service),
) -> ConsumptionResponse:
    """Return time-series energy consumption readings and anomaly flags."""
    result, age = await consumption_service.get_consumption(
        meter_id=meter_id,
        from_date=from_date,
        to_date=to_date,
    )
    response.headers["X-Cache-Age"] = f"{age:.1f}"
    return result
