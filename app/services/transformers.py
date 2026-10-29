"""Transformers service: list distribution transformers with meter counts."""

import asyncio
from app.cache import TTLCache
from app.config import Settings
from app.models import TransformerSummary, TransformersListResponse
from app.portal.client import PortalClient, PortalNotFoundError
from app.services.meters import MetersService


class TransformersService:
    """Service handling distribution transformer aggregations."""

    def __init__(
        self,
        client: PortalClient,
        meters_service: MetersService,
        cache: TTLCache,
        settings: Settings,
    ):
        self.client = client
        self.meters_service = meters_service
        self.cache = cache
        self.settings = settings
        self._load_lock = asyncio.Lock()

    async def get_all_transformers(self) -> tuple[list[TransformerSummary], float]:
        """Fetch all transformers and compute active meter counts per DT."""
        cache_key = "all_transformers"
        cached, age = await self.cache.get(cache_key)
        if cached is not None:
            return cached, age or 0.0

        async with self._load_lock:
            cached, age = await self.cache.get(cache_key)
            if cached is not None:
                return cached, age or 0.0

            # Concurrently fetch DT list and all meters
            dts_page1_task = self.client.list_dts(page=1)
            all_meters_task = self.meters_service.get_all_meters()
            dts_page1, (all_meters, _) = await asyncio.gather(dts_page1_task, all_meters_task)

            raw_dts = list(dts_page1.get("data", []))
            total = dts_page1.get("total", len(raw_dts))
            page_size = 20

            total_pages = (total + page_size - 1) // page_size
            if total_pages > 1:
                tasks = [self.client.list_dts(page=p) for p in range(2, total_pages + 1)]
                results = await asyncio.gather(*tasks)
                for res in results:
                    raw_dts.extend(res.get("data", []))

            # Count meters per dt_code
            meter_counts: dict[str, int] = {}
            for m in all_meters:
                dt_code = m.dt_code.strip()
                meter_counts[dt_code] = meter_counts.get(dt_code, 0) + 1

            transformers: list[TransformerSummary] = []
            for dt in raw_dts:
                code = dt.get("code", "").strip()
                name = dt.get("name", code).strip()
                feeder_code = dt.get("feederCode")
                capacity = dt.get("capacityKva")
                count = meter_counts.get(code, 0)

                transformers.append(
                    TransformerSummary(
                        code=code,
                        name=name,
                        feeder_code=feeder_code,
                        capacity_kva=capacity,
                        meter_count=count,
                    )
                )

            # Sort by code
            transformers.sort(key=lambda t: t.code)

            await self.cache.set(
                cache_key,
                transformers,
                ttl=float(self.settings.metadata_cache_ttl_seconds),
            )
            return transformers, 0.0

    async def list_transformers(
        self,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[TransformersListResponse, float]:
        """Return paginated list of distribution transformers."""
        all_dts, age = await self.get_all_transformers()
        total = len(all_dts)
        start = (page - 1) * page_size
        items = all_dts[start : start + page_size]

        response = TransformersListResponse(
            items=items,
            total=total,
        )
        return response, age

    async def get_transformer(self, code: str) -> tuple[TransformerSummary, float]:
        """Get transformer by code with meter count."""
        all_dts, age = await self.get_all_transformers()
        code_upper = code.strip().upper()

        for dt in all_dts:
            if dt.code.upper() == code_upper:
                return dt, age

        raise PortalNotFoundError(f"Transformer '{code}' not found")
