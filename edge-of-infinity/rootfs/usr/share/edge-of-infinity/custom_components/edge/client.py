"""Async client for Edge of Infinity."""

from __future__ import annotations

import json as json_lib
from dataclasses import dataclass
from pathlib import Path
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

    @property
    def is_local(self) -> bool:
        """Return true when reading mirrored files from Home Assistant config."""
        return self.base_url.lower() in ("", "local", "file", "filesystem")

    def _read_local_json(self, filename: str) -> Any:
        """Read a JSON file mirrored by the Edge add-on."""
        paths = (
            Path("/config/edge") / filename,
            Path("/homeassistant/edge") / filename,
        )
        for path in paths:
            if path.exists():
                return json_lib.loads(path.read_text(encoding="utf-8"))
        raise EdgeConnectionError(
            "Local Edge files were not found. Start the Edge of Infinity add-on first."
        )

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
        if self.is_local:
            return self._read_local_json("health.json")
        return await self._request("GET", "/health")

    async def cameras(self) -> list[dict[str, Any]]:
        """Return cameras from core API or the current add-on shell."""
        if self.is_local:
            payload = self._read_local_json("cameras.json")
            cameras = payload.get("cameras") if isinstance(payload, dict) else payload
            return cameras if isinstance(cameras, list) else []

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
