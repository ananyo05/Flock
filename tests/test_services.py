import asyncio
import json
from pathlib import Path
import pytest
import respx
import httpx

from app.cache import TTLCache
from app.config import Settings
from app.portal.client import PortalClient, PortalNotFoundError
from app.services.consumption import ConsumptionService
from app.services.hierarchy import HierarchyService
from app.services.meters import MetersService, haversine_distance
from app.services.transformers import TransformersService


@pytest.fixture
def test_settings():
    return Settings(
        PORTAL_BASE_URL="https://ops.urja.internal",
        PORTAL_USERNAME="test@urja.local",
        PORTAL_PASSWORD="pass",
        METADATA_CACHE_TTL_SECONDS=900,
        CONSUMPTION_CACHE_TTL_SECONDS=60,
        PORTAL_MAX_CONCURRENCY=4,
        PORTAL_REQUEST_DELAY_SECONDS=0.0,
        PORTAL_TIMEOUT_SECONDS=2.0,
    )


@pytest.fixture
def cache():
    return TTLCache(default_ttl=900.0)


@pytest.fixture
def portal_client(test_settings):
    client = PortalClient(settings=test_settings)
    client._last_login = 9999999999.0  # avoid login during tests
    return client


@pytest.mark.asyncio
async def test_cache_ttl_and_age(cache: TTLCache):
    await cache.set("test_key", "hello_world", ttl=0.1)
    val, age = await cache.get("test_key")
    assert val == "hello_world"
    assert age is not None and age >= 0.0

    await asyncio.sleep(0.15)
    expired_val, _ = await cache.get("test_key")
    assert expired_val is None


@pytest.mark.asyncio
async def test_meters_service_list_and_filters(
    test_settings: Settings,
    cache: TTLCache,
    portal_client: PortalClient,
):
    meters_service = MetersService(portal_client, cache, test_settings)

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/meters/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"meterId": "J100000", "serialNo": "SE33962", "make": "HPL", "phaseType": "single", "installStatus": "Decommissioned", "dtCode": "DT-001"},
                        {"meterId": "J100001", "serialNo": "GE84132", "make": "L&T", "phaseType": "single", "installStatus": "Installed", "dtCode": "DT-002"},
                        {"meterId": "J100002", "serialNo": "AL28136", "make": "L&T", "phaseType": "three", "installStatus": "Installed", "dtCode": "DT-003"},
                    ],
                    "total": 3,
                    "page": 1,
                    "pageSize": 20,
                },
            )
        )

        # Unfiltered list
        res, age = await meters_service.list_meters(page=1, page_size=10)
        assert res.total == 3
        assert len(res.items) == 3

        # Filter by make
        res_make, _ = await meters_service.list_meters(make="L&T")
        assert res_make.total == 2

        # Filter by phase
        res_phase, _ = await meters_service.list_meters(phase="three")
        assert res_phase.total == 1
        assert res_phase.items[0].meter_id == "J100002"

        # Filter by search q
        res_q, _ = await meters_service.list_meters(q="se33962")
        assert res_q.total == 1
        assert res_q.items[0].meter_id == "J100000"


@pytest.mark.asyncio
async def test_meters_service_detail(
    test_settings: Settings,
    cache: TTLCache,
    portal_client: PortalClient,
    samples_dir: Path,
):
    meters_service = MetersService(portal_client, cache, test_settings)

    with open(samples_dir / "detail" / "J100000.json") as f:
        detail_raw = json.load(f)

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/meters/J100000/__data.json").mock(
            return_value=httpx.Response(200, json=detail_raw)
        )
        respx_mock.get("/portal/meters/J100000/geo").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"latitude": "26.9832", "longitude": "75.8816"}},
            )
        )

        detail, age = await meters_service.get_meter_detail("J100000")
        assert detail.meter_id == "J100000"
        assert detail.make == "HPL"
        assert detail.location is not None
        assert detail.location.latitude == pytest.approx(26.9832)
        assert detail.hierarchy.zone is not None
        assert detail.hierarchy.zone.code == "Z-01"


@pytest.mark.asyncio
async def test_meters_service_nearby(
    test_settings: Settings,
    cache: TTLCache,
    portal_client: PortalClient,
):
    meters_service = MetersService(portal_client, cache, test_settings)

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/meters/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"meterId": "J100000", "serialNo": "SE33962", "make": "HPL", "phaseType": "single", "installStatus": "Installed", "dtCode": "DT-001"},
                        {"meterId": "J100001", "serialNo": "GE84132", "make": "L&T", "phaseType": "single", "installStatus": "Installed", "dtCode": "DT-002"},
                    ],
                    "total": 2,
                    "page": 1,
                    "pageSize": 20,
                },
            )
        )
        # J100000 is ~0 km from query center
        respx_mock.get("/portal/meters/J100000/geo").mock(
            return_value=httpx.Response(200, json={"data": {"latitude": "26.9000", "longitude": "75.8000"}})
        )
        # J100001 is far away (~150 km)
        respx_mock.get("/portal/meters/J100001/geo").mock(
            return_value=httpx.Response(200, json={"data": {"latitude": "28.0000", "longitude": "76.8000"}})
        )

        nearby_res, _ = await meters_service.get_nearby_meters(lat=26.9000, lon=75.8000, radius_km=5.0)
        assert nearby_res.total == 1
        assert nearby_res.items[0].meter_id == "J100000"
        assert nearby_res.items[0].distance_km < 0.1


@pytest.mark.asyncio
async def test_consumption_service(
    test_settings: Settings,
    cache: TTLCache,
    portal_client: PortalClient,
    samples_dir: Path,
):
    consumption_service = ConsumptionService(portal_client, cache, test_settings)

    with open(samples_dir / "energy_sample.json") as f:
        energy_data = json.load(f)

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/meters/J100000/energy").mock(
            return_value=httpx.Response(200, json=energy_data)
        )

        res, _ = await consumption_service.get_consumption("J100000", "2026-06-24", "2026-06-26")
        assert res.meter_id == "J100000"
        assert res.interval == "half_hourly"
        assert len(res.readings) == len(energy_data["data"])
        assert res.readings[1].consumption_kwh is not None


@pytest.mark.asyncio
async def test_transformers_service(
    test_settings: Settings,
    cache: TTLCache,
    portal_client: PortalClient,
):
    meters_service = MetersService(portal_client, cache, test_settings)
    transformers_service = TransformersService(portal_client, meters_service, cache, test_settings)

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/meters/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"meterId": "J100000", "serialNo": "SE1", "make": "HPL", "phaseType": "single", "installStatus": "Installed", "dtCode": "DT-001"},
                        {"meterId": "J100001", "serialNo": "SE2", "make": "HPL", "phaseType": "single", "installStatus": "Installed", "dtCode": "DT-001"},
                        {"meterId": "J100002", "serialNo": "SE3", "make": "HPL", "phaseType": "single", "installStatus": "Installed", "dtCode": "DT-002"},
                    ],
                    "total": 3,
                    "page": 1,
                    "pageSize": 20,
                },
            )
        )
        respx_mock.get("/portal/dts").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"code": "DT-001", "name": "Transformer 1", "feederCode": "F-001", "capacityKva": "250"},
                        {"code": "DT-002", "name": "Transformer 2", "feederCode": "F-002", "capacityKva": "100"},
                    ],
                    "total": 2,
                },
            )
        )

        list_res, _ = await transformers_service.list_transformers()
        assert list_res.total == 2
        # DT-001 has 2 meters
        assert list_res.items[0].code == "DT-001"
        assert list_res.items[0].meter_count == 2
        # DT-002 has 1 meter
        assert list_res.items[1].code == "DT-002"
        assert list_res.items[1].meter_count == 1

        # Single DT lookup
        dt1, _ = await transformers_service.get_transformer("DT-001")
        assert dt1.code == "DT-001"
        assert dt1.meter_count == 2

        with pytest.raises(PortalNotFoundError):
            await transformers_service.get_transformer("DT-999")


@pytest.mark.asyncio
async def test_hierarchy_service(
    test_settings: Settings,
    cache: TTLCache,
    portal_client: PortalClient,
    samples_dir: Path,
):
    meters_service = MetersService(portal_client, cache, test_settings)
    hierarchy_service = HierarchyService(
        portal_client, meters_service, cache, test_settings, samples_dir=samples_dir
    )

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        # 2 meters with sample files J100000 and J100004
        respx_mock.get("/portal/meters/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"meterId": "J100000", "serialNo": "SE33962", "make": "HPL", "phaseType": "single", "installStatus": "Decommissioned", "dtCode": "DT-001"},
                        {"meterId": "J100004", "serialNo": "SE65293", "make": "Genus", "phaseType": "single", "installStatus": "Faulty", "dtCode": "DT-005"},
                    ],
                    "total": 2,
                    "page": 1,
                    "pageSize": 20,
                },
            )
        )

        tree_res, _ = await hierarchy_service.get_hierarchy_tree()
        assert tree_res.total_meters == 2
        assert len(tree_res.tree) > 0

        # Zone 1 and Zone 2 exist in J100000 and J100004
        zone_codes = [z.code for z in tree_res.tree]
        assert "Z-01" in zone_codes
        assert "Z-02" in zone_codes

