import asyncio
import hashlib
import hmac
import time
import httpx
import pytest
import respx

from app.config import Settings
from app.portal.client import (
    PortalAuthError,
    PortalClient,
    PortalNotFoundError,
    PortalUnavailableError,
)


@pytest.fixture
def mock_settings():
    return Settings(
        PORTAL_BASE_URL="https://ops.urja.internal",
        PORTAL_USERNAME="test_user@urja.local",
        PORTAL_PASSWORD="test_password",
        PORTAL_MAX_CONCURRENCY=2,
        PORTAL_REQUEST_DELAY_SECONDS=0.0,
        PORTAL_TIMEOUT_SECONDS=2.0,
        PORTAL_MAX_RETRIES=3,
        SESSION_PROACTIVE_REFRESH_SECONDS=100,
    )


@pytest.mark.asyncio
async def test_portal_login_success(mock_settings):
    client = PortalClient(settings=mock_settings)

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        login_route = respx_mock.post("/login").mock(
            return_value=httpx.Response(
                200,
                headers={"Set-Cookie": "__Secure-better-auth.session_token=test-token.sig; Path=/; Secure"},
                json={"success": True},
            )
        )

        await client.login()
        assert login_route.called
        assert client._last_login > 0
        assert not client._is_session_expired_proactively()

    await client.aclose()


@pytest.mark.asyncio
async def test_search_meters_url_encoding(mock_settings):
    client = PortalClient(settings=mock_settings)
    client._last_login = time.time()  # session active

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        search_route = respx_mock.get("/portal/meters/search").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"meterId": "J100003", "serialNo": "L&84997"}], "total": 1, "page": 1, "pageSize": 20},
            )
        )

        # q has an ampersand: 'L&84997'
        result = await client.search_meters(q="L&84997", page=1)
        assert search_route.called
        req = search_route.calls.last.request
        # Check query string has encoded & (%26)
        assert "q=L%2684997" in str(req.url)
        assert result["total"] == 1

    await client.aclose()


@pytest.mark.asyncio
async def test_reactive_relogin_on_401(mock_settings):
    client = PortalClient(settings=mock_settings)
    client._last_login = time.time()

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        login_route = respx_mock.post("/login").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        # First search call returns 401, second (after login) returns 200
        search_route = respx_mock.get("/portal/meters/search").mock(
            side_effect=[
                httpx.Response(401, json={"error": "unauthorized"}),
                httpx.Response(200, json={"data": [], "total": 0, "page": 1, "pageSize": 20}),
            ]
        )

        res = await client.search_meters()
        assert res["total"] == 0
        assert login_route.call_count == 1
        assert search_route.call_count == 2

    await client.aclose()


@pytest.mark.asyncio
async def test_reactive_relogin_on_html_or_redirect(mock_settings):
    client = PortalClient(settings=mock_settings)
    client._last_login = time.time()

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        login_route = respx_mock.post("/login").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        # First returns redirect to /login with HTML
        geo_route = respx_mock.get("/portal/meters/J100001/geo").mock(
            side_effect=[
                httpx.Response(302, headers={"Location": "/login", "Content-Type": "text/html"}, text="<html>Redirect</html>"),
                httpx.Response(200, json={"data": {"latitude": "26.9", "longitude": "75.8"}}),
            ]
        )

        res = await client.meter_geo("J100001")
        assert res["data"]["latitude"] == "26.9"
        assert login_route.call_count == 1
        assert geo_route.call_count == 2

    await client.aclose()


@pytest.mark.asyncio
async def test_transient_retry_on_5xx_and_eventual_success(mock_settings):
    client = PortalClient(settings=mock_settings)
    client._last_login = time.time()

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        route = respx_mock.get("/portal/dts").mock(
            side_effect=[
                httpx.Response(502, text="Bad Gateway"),
                httpx.Response(503, text="Service Unavailable"),
                httpx.Response(200, json={"data": [{"code": "DT-001"}], "total": 1}),
            ]
        )

        res = await client.list_dts(page=1)
        assert res["total"] == 1
        assert route.call_count == 3

    await client.aclose()


@pytest.mark.asyncio
async def test_persistent_5xx_raises_unavailable(mock_settings):
    client = PortalClient(settings=mock_settings)
    client._last_login = time.time()

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/dts").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        with pytest.raises(PortalUnavailableError):
            await client.list_dts(page=1)

    await client.aclose()


@pytest.mark.asyncio
async def test_not_found_raises_not_found_error(mock_settings):
    client = PortalClient(settings=mock_settings)
    client._last_login = time.time()

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        respx_mock.get("/portal/meters/J999999/geo").mock(
            return_value=httpx.Response(404, json={"message": "Not found"})
        )

        with pytest.raises(PortalNotFoundError):
            await client.meter_geo("J999999")

    await client.aclose()


@pytest.mark.asyncio
async def test_hmac_signed_export_request(mock_settings):
    client = PortalClient(settings=mock_settings)
    client._last_login = time.time()
    secret = "my-secret-key-12345"

    with respx.mock(base_url="https://ops.urja.internal") as respx_mock:
        keys_route = respx_mock.get("/portal/keys").mock(
            return_value=httpx.Response(200, json={"data": {"signingSecret": secret}})
        )

        export_route = respx_mock.get("/portal/export").mock(
            return_value=httpx.Response(200, json={"data": [{"meterId": "J100000"}], "total": 1})
        )

        res = await client.export_meters(page=1)
        assert res["total"] == 1
        assert keys_route.called
        assert export_route.called

        # Inspect headers sent
        req = export_route.calls.last.request
        ts = req.headers.get("x-timestamp")
        sig = req.headers.get("x-signature")
        assert ts is not None
        assert sig is not None

        # Verify signature matching algorithm
        expected_str = f"GET\n/portal/export\npage=1\n{ts}"
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            expected_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert sig == expected_sig

    await client.aclose()
