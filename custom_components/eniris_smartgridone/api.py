"""Async client for the Eniris APIs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession

from .const import API_BASE_URL, AUTH_BASE_URL

_LOGGER = logging.getLogger(__name__)


class EnirisApiError(Exception):
    """Base exception raised for Eniris API failures."""


class EnirisAuthError(EnirisApiError):
    """Raised when Eniris authentication fails."""


class EnirisRateLimitError(EnirisApiError):
    """Raised when Eniris asks the integration to slow down."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class EnirisTwoFactorRequired(EnirisAuthError):
    """Raised when the account requires a 2FA flow that is not configured."""


def normalize_token(raw: str) -> str:
    """Normalize token responses returned as either text or a JSON string."""
    token = raw.strip()
    if token.startswith('"') and token.endswith('"'):
        token = token[1:-1]
    return token


class EnirisAuthClient:
    """Client for the Eniris authentication API."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def login(self, username: str, password: str) -> str:
        """Return a refresh token for username/password credentials."""
        response = await self._session.post(
            f"{AUTH_BASE_URL}/auth/login",
            json={"username": username, "password": password},
            timeout=30,
        )
        return await self._read_token_response(response)

    async def access_token(self, refresh_token: str) -> str:
        """Return a short-lived access token."""
        response = await self._session.get(
            f"{AUTH_BASE_URL}/auth/accesstoken",
            headers={"Authorization": f"Bearer {refresh_token}"},
            timeout=30,
        )
        return await self._read_token_response(response)

    async def _read_token_response(self, response: ClientResponse) -> str:
        async with response:
            text = await response.text()
            if response.status == 200:
                token = normalize_token(text)
                if not token:
                    raise EnirisAuthError("Eniris returned an empty token")
                return token

            error = await _error_message(response, text)
            if response.status == 401:
                if "2FA" in error.upper() or "PARTIAL" in error.upper():
                    raise EnirisTwoFactorRequired(error)
                raise EnirisAuthError(error)
            if response.status == 429:
                raise EnirisRateLimitError(error, await _retry_after(response, text))
            raise EnirisApiError(error)


class EnirisApiClient:
    """Client for Eniris metadata and telemetry APIs."""

    def __init__(
        self,
        session: ClientSession,
        refresh_token: str,
        auth_client: EnirisAuthClient | None = None,
    ) -> None:
        self._session = session
        self._refresh_token = refresh_token
        self._auth_client = auth_client or EnirisAuthClient(session)
        self._access_token: str | None = None
        self._token_lock = asyncio.Lock()

    async def async_get_access_token(self, *, force_refresh: bool = False) -> str:
        """Return an access token, refreshing it if needed."""
        async with self._token_lock:
            if self._access_token and not force_refresh:
                return self._access_token
            self._access_token = await self._auth_client.access_token(self._refresh_token)
            return self._access_token

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        retry_auth: bool = True,
    ) -> Any:
        """Execute an authenticated metadata API request."""
        token = await self.async_get_access_token()
        try:
            response = await self._session.request(
                method,
                f"{API_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=json,
                timeout=30,
            )
        except ClientError as err:
            raise EnirisApiError(f"Failed to connect to Eniris: {err}") from err

        async with response:
            if response.status == 204:
                return None

            text = await response.text()
            if response.status == 401 and retry_auth:
                _LOGGER.debug("Refreshing expired Eniris access token")
                await self.async_get_access_token(force_refresh=True)
                return await self.request(method, path, json=json, retry_auth=False)

            if 200 <= response.status < 300:
                if not text:
                    return None
                content_type = response.headers.get("Content-Type", "")
                if "json" in content_type:
                    return await response.json()
                return normalize_token(text)

            error = await _error_message(response, text)
            if response.status == 401:
                raise EnirisAuthError(error)
            if response.status == 429:
                raise EnirisRateLimitError(error, await _retry_after(response, text))
            raise EnirisApiError(error)

    async def companies(self) -> list[dict[str, Any]]:
        """Return companies associated with the authenticated user."""
        payload = await self.request("GET", "/v1/company")
        if not isinstance(payload, dict):
            return []
        companies = payload.get("company")
        return companies if isinstance(companies, list) else []

    async def roles(self) -> list[dict[str, Any]]:
        """Return roles associated with the authenticated user."""
        payload = await self.request("POST", "/v1/role/query", json={})
        if not isinstance(payload, dict):
            return []
        roles = payload.get("role")
        return roles if isinstance(roles, list) else []

    async def monitors(self, role_id: int | str = "*") -> list[dict[str, Any]]:
        """Return monitor relations for roles associated with the user."""
        payload = await self.request("POST", f"/v1/role/{role_id}/monitors/query", json={})
        if not isinstance(payload, dict):
            return []
        monitors = payload.get("monitors")
        return monitors if isinstance(monitors, list) else []

    async def devices(self, skip_hash: str | None = None) -> dict[str, Any] | None:
        """Return devices monitored by the authenticated user."""
        body: dict[str, Any] = {}
        if skip_hash:
            body["skipHash"] = skip_hash
        payload = await self.request("POST", "/v1/device/query", json=body)
        return payload if isinstance(payload, dict) else None

    async def telemetry(self, queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Run telemetry queries."""
        if not queries:
            return []
        payload = await self.request("POST", "/v1/telemetry/query", json=queries)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]
        return []


async def _error_message(response: ClientResponse, text: str) -> str:
    try:
        payload = await response.json(content_type=None)
    except Exception:
        payload = None

    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        if message:
            return str(message)

    return text.strip() or f"Eniris API returned HTTP {response.status}"


async def _retry_after(response: ClientResponse, text: str) -> int | None:
    header = response.headers.get("Retry-After")
    if header and header.isdigit():
        return int(header)

    try:
        payload = await response.json(content_type=None)
    except Exception:
        _LOGGER.debug("Could not parse rate-limit payload: %s", text)
        return None

    retry_after = payload.get("retryAfter") if isinstance(payload, dict) else None
    return int(retry_after) if isinstance(retry_after, int | float) else None
