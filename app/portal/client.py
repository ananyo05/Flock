"""HTTP client for interacting with the legacy Urja Meter Ops portal."""

import asyncio
from datetime import datetime
import hashlib
import hmac
import logging
import time
from typing import Any
import urllib.parse

import httpx

from app.config import Settings, get_settings
from app.portal.devalue import decode_sveltekit_page_data

logger = logging.getLogger(__name__)


class PortalError(Exception):
    """Base exception for portal communication failures."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class PortalAuthError(PortalError):
    """Raised when portal authentication fails or session cannot be renewed."""

    def __init__(self, message: str = "Portal authentication failed"):
        super().__init__(message, status_code=502)


class PortalNotFoundError(PortalError):
    """Raised when a portal resource is not found (404)."""

    def __init__(self, message: str = "Resource not found on portal"):
        super().__init__(message, status_code=404)


class PortalUnavailableError(PortalError):
    """Raised when portal is unreachable or returns persistent 5xx."""

    def __init__(self, message: str = "Urja portal is currently unavailable"):
        super().__init__(message, status_code=502)


class PortalClient:
    """Async client communicating with the legacy Urja Meter Ops web portal."""

    def __init__(self, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.portal_base_url.rstrip("/")
        self.username = self.settings.portal_username
        self.password = self.settings.portal_password

        # Bounded concurrency semaphore
        self._semaphore = asyncio.Semaphore(self.settings.portal_max_concurrency)
        self._delay = self.settings.portal_request_delay_seconds

        # Concurrency lock for login/re-login to prevent duplicate concurrent logins
        self._login_lock = asyncio.Lock()
        self._last_login: float = 0.0

        # Cached signing secret for HMAC signed exports
        self._signing_secret: str | None = None

        # Underlying httpx client with cookie jar
        self._client = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.settings.portal_timeout_seconds),
            follow_redirects=False,
            cookies=httpx.Cookies(),
        )

    async def aclose(self) -> None:
        """Close underlying httpx client."""
        await self._client.aclose()

    async def __aenter__(self) -> "PortalClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()

    def _is_session_expired_proactively(self) -> bool:
        """Check if proactive refresh interval (~50 minutes) has elapsed."""
        if self._last_login == 0:
            return True
        return (time.time() - self._last_login) >= self.settings.session_proactive_refresh_seconds

    async def login(self, force: bool = False) -> None:
        """Authenticate with legacy portal via POST /login.

        Sends form-urlencoded email and password with SvelteKit action headers.
        Updates httpx cookie jar with session token.
        """
        async with self._login_lock:
            # Check if another task already logged in while waiting for lock
            if not force and not self._is_session_expired_proactively():
                return

            login_url = f"{self.base_url}/login"
            headers = {
                "Accept": "application/json",
                "x-sveltekit-action": "true",
            }
            data = {
                "email": self.username,
                "password": self.password,
            }

            try:
                response = await self._client.post(
                    login_url,
                    headers=headers,
                    data=data,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                raise PortalUnavailableError(f"Network error during portal login: {str(e)}") from e

            # SvelteKit action success returns 200 (or redirect with Set-Cookie)
            if response.status_code not in (200, 204, 302, 303):
                raise PortalAuthError(
                    f"Portal login failed with HTTP {response.status_code}: {response.text[:200]}"
                )

            # Invalidate cached signing secret on new login session
            self._signing_secret = None
            self._last_login = time.time()
            logger.info("Successfully authenticated with Urja portal session")

    def _is_auth_failure(self, response: httpx.Response) -> bool:
        """Detect whether a response signals an invalid/expired session."""
        if response.status_code in (401, 403):
            return True

        # Redirect to /login
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            if "login" in location:
                return True

        # HTML response when expecting JSON
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type:
            return True

        # Checking response body preview for html tag
        if response.content.startswith(b"<!DOCTYPE") or response.content.startswith(b"<html"):
            return True

        return False

    async def _execute_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retry_auth: bool = True,
    ) -> httpx.Response:
        """Execute request with politeness throttling, transient retries, and auth recovery."""
        # Proactive login check
        if self._is_session_expired_proactively():
            await self.login()

        req_headers = {"Accept": "application/json"}
        if headers:
            req_headers.update(headers)

        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"

        max_tries = self.settings.portal_max_retries
        last_exception: Exception | None = None

        for attempt in range(max_tries):
            # Apply bounded concurrency semaphore and polite delay
            async with self._semaphore:
                if self._delay > 0:
                    await asyncio.sleep(self._delay)

                try:
                    response = await self._client.request(
                        method=method,
                        url=url,
                        params=params,
                        headers=req_headers,
                    )
                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    last_exception = e
                    if attempt < max_tries - 1:
                        backoff = 0.1 * (2**attempt)
                        await asyncio.sleep(backoff)
                        continue
                    raise PortalUnavailableError(f"Request failed after {max_tries} retries: {str(e)}") from e

            # Handle 5xx server errors with retry
            if response.status_code >= 500:
                if attempt < max_tries - 1:
                    backoff = 0.1 * (2**attempt)
                    await asyncio.sleep(backoff)
                    continue
                raise PortalUnavailableError(
                    f"Portal returned server error {response.status_code}: {response.text[:200]}"
                )

            # Check for auth failure
            if self._is_auth_failure(response):
                if retry_auth:
                    logger.warning("Session failure detected; re-authenticating and retrying once")
                    await self.login(force=True)
                    return await self._execute_request(
                        method=method,
                        path=path,
                        params=params,
                        headers=headers,
                        retry_auth=False,
                    )
                raise PortalAuthError(f"Authentication failure accessing {path}: {response.status_code}")

            if response.status_code == 404:
                raise PortalNotFoundError(f"Resource not found: {path}")

            if response.status_code >= 400:
                raise PortalError(
                    f"Portal returned error {response.status_code} for {path}: {response.text[:200]}",
                    status_code=response.status_code,
                )

            return response

        if last_exception:
            raise PortalUnavailableError(f"Request failed: {str(last_exception)}") from last_exception
        raise PortalUnavailableError(f"Portal request to {path} failed")

    async def search_meters(self, q: str = "", page: int = 1) -> dict[str, Any]:
        """Search meters by query string with pagination."""
        params = {"q": q, "page": page}
        resp = await self._execute_request("GET", "/portal/meters/search", params=params)
        return resp.json()

    async def meter_detail(self, meter_id: str) -> dict[str, Any]:
        """Fetch and decode meter detail via SvelteKit __data.json."""
        encoded_id = urllib.parse.quote(meter_id, safe="")
        path = f"/meters/{encoded_id}/__data.json"
        resp = await self._execute_request("GET", path)
        try:
            data_json = resp.json()
        except Exception as e:
            raise PortalError(f"Failed to parse __data.json for meter {meter_id}: {str(e)}") from e
        return decode_sveltekit_page_data(data_json)

    async def meter_geo(self, meter_id: str) -> dict[str, Any]:
        """Fetch meter coordinates from /portal/meters/{id}/geo."""
        encoded_id = urllib.parse.quote(meter_id, safe="")
        path = f"/portal/meters/{encoded_id}/geo"
        resp = await self._execute_request("GET", path)
        return resp.json()

    async def meter_energy(
        self,
        meter_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """Fetch energy readings from /portal/meters/{id}/energy."""
        encoded_id = urllib.parse.quote(meter_id, safe="")
        path = f"/portal/meters/{encoded_id}/energy"
        params: dict[str, str] = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        resp = await self._execute_request("GET", path, params=params)
        return resp.json()

    async def list_dts(self, page: int = 1) -> dict[str, Any]:
        """Fetch distribution transformers from /portal/dts."""
        params = {"page": page}
        resp = await self._execute_request("GET", "/portal/dts", params=params)
        return resp.json()

    async def get_signing_secret(self, force_refresh: bool = False) -> str:
        """Fetch export HMAC signing secret from /portal/keys."""
        if not force_refresh and self._signing_secret:
            return self._signing_secret

        resp = await self._execute_request("GET", "/portal/keys")
        payload = resp.json()
        secret = payload.get("data", {}).get("signingSecret")
        if not secret:
            raise PortalError("Portal keys endpoint did not return signingSecret")

        self._signing_secret = secret
        return secret

    async def export_meters(self, page: int = 1) -> dict[str, Any]:
        """Fetch meters using the HMAC-SHA256 signed export route."""
        secret = await self.get_signing_secret()
        timestamp = str(int(time.time()))
        query_string = f"page={page}"
        path = "/portal/export"

        # StringToSign: METHOD\nPATH\nQUERYSTRING\nTIMESTAMP
        string_to_sign = f"GET\n{path}\n{query_string}\n{timestamp}"
        signature = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "x-timestamp": timestamp,
            "x-signature": signature,
        }

        try:
            resp = await self._execute_request(
                "GET",
                path,
                params={"page": page},
                headers=headers,
            )
            return resp.json()
        except PortalError as e:
            # If rejected (secret might have rotated), retry once with refreshed secret
            if e.status_code in (401, 403):
                logger.info("Export signature rejected; refreshing signing secret and retrying")
                new_secret = await self.get_signing_secret(force_refresh=True)
                new_ts = str(int(time.time()))
                new_str = f"GET\n{path}\n{query_string}\n{new_ts}"
                new_sig = hmac.new(
                    new_secret.encode("utf-8"),
                    new_str.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                new_headers = {
                    "x-timestamp": new_ts,
                    "x-signature": new_sig,
                }
                resp = await self._execute_request(
                    "GET",
                    path,
                    params={"page": page},
                    headers=new_headers,
                )
                return resp.json()
            raise

