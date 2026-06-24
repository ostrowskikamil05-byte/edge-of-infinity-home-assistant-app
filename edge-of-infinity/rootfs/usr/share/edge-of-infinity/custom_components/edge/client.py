"""Async client for Edge of Infinity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession


class EdgeClientError(Exception):
    """Base Edge client error."""


class EdgeAuthError(EdgeClientError):
    """Authentication failed."""


class EdgeConnectionError(EdgeClientError):
    """Connection failed."""


@dataclass(slots=True)
class EdgeClient:
    """Small HTTP client for the Edge app/core."""

    base_url: str
    session: ClientSession
    api_key: str | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expect_json: bool = True,
    ) -> Any:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=15,
            ) as response:
                if response.status in (401, 403):
                    raise EdgeAuthError("Edge rejected the credentials")
                response.raise_for_status()
                if expect_json:
                    return await response.json()
                return await response.read()
        except ClientResponseError as err:
            raise EdgeConnectionError(f"Edge returned HTTP {err.status}") from err
        except ClientError as err:
            raise EdgeConnectionError("Unable to connect to Edge") from err

    async def health(self) -> dict[str, Any]:
        """Return Edge health."""
        return await self._request("GET", "/health")

    async def cameras(self) -> list[dict[str, Any]]:
        """Return cameras from core API or the current add-on shell."""
        try:
            payload = await self._request("GET", "/cameras")
        except EdgeConnectionError:
            payload = await self._request("GET", "/cameras.json")

        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            cameras = payload.get("cameras")
            if isinstance(cameras, list):
                return cameras
        return []
