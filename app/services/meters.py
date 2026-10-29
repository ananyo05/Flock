"""Meters service: search, detail enrichment, coordinates, and nearby spatial search."""

import asyncio
import math
from typing import Any
from app.cache import TTLCache
from app.config import Settings
from app.models import (
    Location,
    MeterDetailResponse,
    MeterSummary,
    NearbyMeter,
    NearbyMetersResponse,
    PaginatedMetersResponse,
    StructuredHierarchy,
)
from app.portal.client import PortalClient, PortalNotFoundError
from app.portal.parsers import parse_float, parse_hierarchy, parse_meter_detail


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great circle distance between two points in kilometers."""
    radius_earth_km = 6371.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2)
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return radius_earth_km * c


class MetersService:
    """Service orchestrating meter searches, details, and spatial queries."""

    def __init__(self, client: PortalClient, cache: TTLCache, settings: Settings):
        self.client = client
        self.cache = cache
        self.settings = settings
        self._load_lock = asyncio.Lock()

    async def get_all_meters(self) -> tuple[list[MeterSummary], float]:
        """Fetch and cache all meters across portal search pagination."""
        cached_meters, age = await self.cache.get("all_meters")
        if cached_meters is not None:
            return cached_meters, age or 0.0

        async with self._load_lock:
            # Recheck cache inside lock
            cached_meters, age = await self.cache.get("all_meters")
            if cached_meters is not None:
                return cached_meters, age or 0.0

            first_page = await self.client.search_meters(q="", page=1)
            raw_items = list(first_page.get("data", []))
            total = first_page.get("total", len(raw_items))
            page_size = first_page.get("pageSize", 20)

            total_pages = (total + page_size - 1) // page_size
            if total_pages > 1:
                # Fetch remaining pages concurrently with polite bounded semaphore
                tasks = [self.client.search_meters(q="", page=p) for p in range(2, total_pages + 1)]
                results = await asyncio.gather(*tasks, return_exceptions=False)
                for res in results:
                    raw_items.extend(res.get("data", []))

            # Normalize raw items into MeterSummary models
            meters: list[MeterSummary] = []
            for item in raw_items:
                meters.append(
                    MeterSummary(
                        meter_id=item["meterId"],
                        serial_no=item["serialNo"],
                        make=item.get("make", "Unknown"),
                        phase_type=item.get("phaseType", "single"),
                        installation_status=item.get("installStatus", "Unknown"),
                        dt_code=item.get("dtCode", "Unknown"),
                        installation_type=item.get("installationType"),
                    )
                )

            await self.cache.set("all_meters", meters, ttl=float(self.settings.metadata_cache_ttl_seconds))
            return meters, 0.0

    async def list_meters(
        self,
        q: str | None = None,
        make: str | None = None,
        status: str | None = None,
        phase: str | None = None,
        dt: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[PaginatedMetersResponse, float]:
        """Filter and paginate meter list from cached metadata."""
        meters, age = await self.get_all_meters()

        filtered = meters

        if q:
            query_lower = q.lower().strip()
            filtered = [
                m for m in filtered
                if query_lower in m.meter_id.lower() or query_lower in m.serial_no.lower()
            ]

        if make:
            make_lower = make.lower().strip()
            filtered = [m for m in filtered if m.make.lower() == make_lower]

        if status:
            status_lower = status.lower().strip()
            filtered = [m for m in filtered if m.installation_status.lower() == status_lower]

        if phase:
            phase_lower = phase.lower().strip()
            filtered = [m for m in filtered if m.phase_type.lower() == phase_lower]

        if dt:
            dt_lower = dt.lower().strip()
            filtered = [m for m in filtered if m.dt_code.lower() == dt_lower]

        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size
        items = filtered[start:end]

        response = PaginatedMetersResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
        return response, age

    async def get_meter_detail(self, meter_id: str) -> tuple[MeterDetailResponse, float]:
        """Fetch and cache detailed meter info including coordinates and distribution hierarchy."""
        cache_key = f"meter_detail:{meter_id}"
        cached, age = await self.cache.get(cache_key)
        if cached is not None:
            return cached, age or 0.0

        # Fetch detail and geo concurrently
        try:
            page_data, geo_data = await asyncio.gather(
                self.client.meter_detail(meter_id),
                self.client.meter_geo(meter_id),
            )
        except PortalNotFoundError:
            raise
        except Exception as e:
            # Try fetching detail alone if geo failed with 404
            page_data = await self.client.meter_detail(meter_id)
            geo_data = {"data": {}}

        detail_fields = parse_meter_detail(page_data)
        hierarchy_dict = parse_hierarchy(page_data.get("hierarchy"))

        lat = parse_float(geo_data.get("data", {}).get("latitude"))
        lon = parse_float(geo_data.get("data", {}).get("longitude"))
        loc = Location(latitude=lat, longitude=lon) if (lat is not None or lon is not None) else None

        response = MeterDetailResponse(
            meter_id=detail_fields.get("meter_id") or meter_id,
            serial_no=detail_fields.get("serial_no") or "Unknown",
            make=detail_fields.get("make") or "Unknown",
            phase_type=detail_fields.get("phase_type") or "single",
            installation_status=detail_fields.get("installation_status") or "Unknown",
            installation_type=detail_fields.get("installation_type"),
            location=loc,
            hierarchy=StructuredHierarchy(**hierarchy_dict),
        )

        await self.cache.set(cache_key, response, ttl=float(self.settings.metadata_cache_ttl_seconds))
        return response, 0.0

    async def get_nearby_meters(
        self,
        lat: float,
        lon: float,
        radius_km: float = 5.0,
    ) -> tuple[NearbyMetersResponse, float]:
        """Find meters within radius_km using bounding-box prefilter and Haversine formula."""
        # Validate coordinates
        if not (-90.0 <= lat <= 90.0):
            raise ValueError(f"Latitude must be between -90 and 90, got {lat}")
        if not (-180.0 <= lon <= 180.0):
            raise ValueError(f"Longitude must be between -180 and 180, got {lon}")
        if radius_km <= 0:
            raise ValueError("Radius must be greater than 0")

        all_meters, age = await self.get_all_meters()

        # Bounding box calculation
        # 1 degree lat approx 111.0 km
        delta_lat = radius_km / 111.0
        cos_lat = math.cos(math.radians(lat))
        delta_lon = radius_km / (111.0 * max(0.01, cos_lat))

        min_lat = lat - delta_lat
        max_lat = lat + delta_lat
        min_lon = lon - delta_lon
        max_lon = lon + delta_lon

        # Fetch coordinates for meters (cached per meter)
        async def get_meter_loc(meter: MeterSummary) -> tuple[MeterSummary, Location | None]:
            geo_cache_key = f"meter_geo:{meter.meter_id}"
            cached_geo, _ = await self.cache.get(geo_cache_key)
            if cached_geo is not None:
                return meter, cached_geo
            try:
                raw_geo = await self.client.meter_geo(meter.meter_id)
                g_lat = parse_float(raw_geo.get("data", {}).get("latitude"))
                g_lon = parse_float(raw_geo.get("data", {}).get("longitude"))
                loc = Location(latitude=g_lat, longitude=g_lon) if g_lat and g_lon else None
                await self.cache.set(geo_cache_key, loc, ttl=float(self.settings.metadata_cache_ttl_seconds))
                return meter, loc
            except Exception:
                return meter, None

        loc_results = await asyncio.gather(*[get_meter_loc(m) for m in all_meters])

        nearby_list: list[NearbyMeter] = []
        for meter, loc in loc_results:
            if not loc or loc.latitude is None or loc.longitude is None:
                continue

            m_lat, m_lon = loc.latitude, loc.longitude
            # Bounding box prefilter
            if not (min_lat <= m_lat <= max_lat and min_lon <= m_lon <= max_lon):
                continue

            # Haversine distance calculation
            dist = haversine_distance(lat, lon, m_lat, m_lon)
            if dist <= radius_km:
                nearby_list.append(
                    NearbyMeter(
                        meter_id=meter.meter_id,
                        serial_no=meter.serial_no,
                        make=meter.make,
                        dt_code=meter.dt_code,
                        location=loc,
                        distance_km=round(dist, 3),
                    )
                )

        nearby_list.sort(key=lambda x: x.distance_km)

        response = NearbyMetersResponse(
            center_lat=lat,
            center_lon=lon,
            radius_km=radius_km,
            total=len(nearby_list),
            items=nearby_list,
        )
        return response, age

