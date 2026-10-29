"""Consumption service: energy readings, interval resolution, deltas, and anomalies."""

from app.cache import TTLCache
from app.config import Settings
from app.models import ConsumptionResponse, EnergyReading
from app.portal.client import PortalClient
from app.portal.parsers import process_energy_readings, validate_date_range


class ConsumptionService:
    """Service handling time-series consumption registers and anomaly detection."""

    def __init__(self, client: PortalClient, cache: TTLCache, settings: Settings):
        self.client = client
        self.cache = cache
        self.settings = settings

    async def get_consumption(
        self,
        meter_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> tuple[ConsumptionResponse, float]:
        """Fetch, calculate deltas, and return consumption time-series for a meter."""
        # Strictly validate dates (throws ValueError for bad formats or from > to)
        valid_from, valid_to = validate_date_range(from_date, to_date)

        cache_key = f"consumption:{meter_id}:{valid_from}:{valid_to}"
        cached, age = await self.cache.get(cache_key)
        if cached is not None:
            return cached, age or 0.0

        raw = await self.client.meter_energy(
            meter_id=meter_id,
            from_date=valid_from,
            to_date=valid_to,
        )

        readings_raw = raw.get("data", [])
        processed_readings, interval = process_energy_readings(readings_raw)

        readings_models = [EnergyReading(**item) for item in processed_readings]

        response = ConsumptionResponse(
            meter_id=meter_id,
            interval=interval,  # type: ignore[arg-type]
            from_date=valid_from,
            to_date=valid_to,
            readings=readings_models,
        )

        await self.cache.set(
            cache_key,
            response,
            ttl=float(self.settings.consumption_cache_ttl_seconds),
        )
        return response, 0.0
