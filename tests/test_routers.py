import json
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient
import respx
import httpx

from app.cache import TTLCache
from app.config import Settings
from app.main import app
from app.portal.client import PortalClient
from app.services.consumption import ConsumptionService
from app.services.hierarchy import HierarchyService
from app.services.meters import MetersService
from app.services.transformers import TransformersService


@pytest.fixture
async def async_client(samples_dir: Path):
    settings = Settings(
        PORTAL_BASE_URL="https://ops.urja.internal",
        PORTAL_USERNAME="test@urja.local",
        PORTAL_PASSWORD="pass",
        METADATA_CACHE_TTL_SECONDS=900,
        CONSUMPTION_CACHE_TTL_SECONDS=60,
        PORTAL_MAX_CONCURRENCY=4,
        PORTAL_REQUEST_DELAY_SECONDS=0.0,
        PORTAL_TIMEOUT_SECONDS=2.0,
    )
    cache = TTLCache(default_ttl=900.0)
    portal_client = PortalClient(settings=settings)
    portal_client._last_login = 9999999999.0  # mock already authenticated

    meters_service = MetersService(portal_client, cache, settings)
    consumption_service = ConsumptionService(portal_client, cache, settings)
    transformers_service = TransformersService(portal_client, meters_service, cache, settings)
    hierarchy_service = HierarchyService(
        portal_client, meters_service, cache, settings, samples_dir=samples_dir
    )

    app.state.settings = settings
    app.state.cache = cache
    app.state.portal_client = portal_client
    app.state.meters_service = meters_service
    app.state.consumption_service = consumption_service
    app.state.transformers_service = transformers_service
    app.state.hierarchy_service = hierarchy_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await portal_client.aclose()


@pytest.mark.asyncio
async def test_router_health(async_client: AsyncClient):
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_router_get_meters(async_client: AsyncClient):
    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/meters/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"meterId": "J100000", "serialNo": "SE33962", "make": "HPL", "phaseType": "single", "installStatus": "Decommissioned", "dtCode": "DT-001"},
                        {"meterId": "J100001", "serialNo": "GE84132", "make": "L&T", "phaseType": "single", "installStatus": "Installed", "dtCode": "DT-002"},
                    ],
                    "total": 2,
                    "page": 1,
                    "pageSize": 20,
                },
            )
        )

        resp = await async_client.get("/meters?page=1&page_size=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2
        assert "X-Cache-Age" in resp.headers


@pytest.mark.asyncio
async def test_router_get_meter_detail(async_client: AsyncClient, samples_dir: Path):
    with open(samples_dir / "detail" / "J100000.json") as f:
        detail_raw = json.load(f)

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/meters/J100000/__data.json").mock(
            return_value=httpx.Response(200, json=detail_raw)
        )
        respx_mock.get("/portal/meters/J100000/geo").mock(
            return_value=httpx.Response(200, json={"data": {"latitude": "26.9832", "longitude": "75.8816"}})
        )

        resp = await async_client.get("/meters/J100000")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meter_id"] == "J100000"
        assert data["make"] == "HPL"
        assert data["location"]["latitude"] == pytest.approx(26.9832)
        assert data["hierarchy"]["zone"]["code"] == "Z-01"
        assert "X-Cache-Age" in resp.headers


@pytest.mark.asyncio
async def test_router_get_meter_detail_not_found(async_client: AsyncClient):
    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/meters/UNKNOWN/__data.json").mock(
            return_value=httpx.Response(404, json={"message": "Not found"})
        )

        resp = await async_client.get("/meters/UNKNOWN")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_router_get_meter_consumption(async_client: AsyncClient, samples_dir: Path):
    with open(samples_dir / "energy_sample.json") as f:
        energy_data = json.load(f)

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/meters/J100000/energy").mock(
            return_value=httpx.Response(200, json=energy_data)
        )

        resp = await async_client.get("/meters/J100000/consumption?from=2026-06-24&to=2026-06-26")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meter_id"] == "J100000"
        assert data["interval"] == "half_hourly"
        assert len(data["readings"]) > 0
        assert "X-Cache-Age" in resp.headers


@pytest.mark.asyncio
async def test_router_get_meter_consumption_bad_date_format(async_client: AsyncClient):
    # Non YYYY-MM-DD should return 400
    resp = await async_client.get("/meters/J100000/consumption?from=24/06/2026")
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"]["code"] == "BAD_REQUEST"

    # from > to should return 400
    resp_inverted = await async_client.get("/meters/J100000/consumption?from=2026-06-26&to=2026-06-24")
    assert resp_inverted.status_code == 400
    assert resp_inverted.json()["error"]["code"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_router_get_nearby_meters(async_client: AsyncClient):
    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/meters/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"meterId": "J100000", "serialNo": "SE1", "make": "HPL", "phaseType": "single", "installStatus": "Installed", "dtCode": "DT-001"},
                    ],
                    "total": 1,
                    "page": 1,
                    "pageSize": 20,
                },
            )
        )
        respx_mock.get("/portal/meters/J100000/geo").mock(
            return_value=httpx.Response(200, json={"data": {"latitude": "26.9000", "longitude": "75.8000"}})
        )

        resp = await async_client.get("/meters/nearby?lat=26.9000&lon=75.8000&radius_km=10.0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["meter_id"] == "J100000"


@pytest.mark.asyncio
async def test_router_get_transformers(async_client: AsyncClient):
    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/meters/search").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"meterId": "J100000", "serialNo": "S1", "make": "HPL", "phaseType": "single", "installStatus": "Installed", "dtCode": "DT-001"}], "total": 1, "page": 1, "pageSize": 20},
            )
        )
        respx_mock.get("/portal/dts").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"code": "DT-001", "name": "DT 1", "feederCode": "F-001", "capacityKva": "100"}], "total": 1},
            )
        )

        # List
        resp = await async_client.get("/transformers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["code"] == "DT-001"
        assert data["items"][0]["meter_count"] == 1

        # Single DT
        resp_dt = await async_client.get("/transformers/DT-001")
        assert resp_dt.status_code == 200
        assert resp_dt.json()["code"] == "DT-001"


@pytest.mark.asyncio
async def test_router_get_hierarchy(async_client: AsyncClient):
    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/meters/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"meterId": "J100000", "serialNo": "SE33962", "make": "HPL", "phaseType": "single", "installStatus": "Decommissioned", "dtCode": "DT-001"},
                    ],
                    "total": 1,
                    "page": 1,
                    "pageSize": 20,
                },
            )
        )

        resp = await async_client.get("/hierarchy")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_meters"] == 1
        assert len(data["tree"]) > 0
        assert "data_quality" in data


@pytest.mark.asyncio
async def test_router_admin_refresh(async_client: AsyncClient):
    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/meters/search").mock(
            return_value=httpx.Response(
                200,
                json={"data": [], "total": 0, "page": 1, "pageSize": 20},
            )
        )
        respx_mock.get("/portal/dts").mock(
            return_value=httpx.Response(
                200,
                json={"data": [], "total": 0},
            )
        )

        resp = await async_client.post("/admin/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert "successfully invalidated" in data["status"]


@pytest.mark.asyncio
async def test_router_portal_unavailable_502(async_client: AsyncClient):
    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/meters/search").mock(
            return_value=httpx.Response(503, text="Service Unavailable")
        )

        resp = await async_client.get("/meters")
        assert resp.status_code == 502
        data = resp.json()
        assert data["error"]["code"] == "BAD_GATEWAY"

