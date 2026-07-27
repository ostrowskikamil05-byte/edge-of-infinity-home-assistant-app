"""Async client for Edge of Infinity."""

from __future__ import annotations

import json as json_lib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError, ClientResponseError, ClientSession

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

HIKVISION_MAIN_CHANNEL = "101"
HIKVISION_SUB_CHANNEL = "102"
CONFIG_CAMERA_FIELDS = (
    "id",
    "name",
    "vendor",
    "host",
    "username",
    "rtsp_main",
    "rtsp_sub",
    "camera_number",
    "access_protocol",
    "rtsp_transport",
    "rtsp_main_channel",
    "rtsp_sub_channel",
    "onvif_url",
    "isapi_base_url",
    "enabled",
    "record",
    "low_latency",
    "snapshot_stream",
    "live_stream",
    "tile_stream",
    "record_stream",
)


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
    hass: "HomeAssistant | None" = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    @property
    def is_local(self) -> bool:
        """Return true when reading mirrored files from Home Assistant config."""
        return self.base_url.lower() in ("", "local", "file", "filesystem")

    @staticmethod
    def _read_local_json_sync(filename: str) -> Any:
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

    async def _read_local_json(self, filename: str) -> Any:
        """Read a local JSON file without blocking Home Assistant's event loop."""
        if self.hass is None:
            return self._read_local_json_sync(filename)
        return await self.hass.async_add_executor_job(self._read_local_json_sync, filename)

    @staticmethod
    def _read_local_bytes_sync(filename: str) -> bytes:
        """Read a binary file mirrored by the Edge add-on."""
        relative_path = Path(filename)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise EdgeConnectionError("Invalid local Edge file path.")

        paths = (
            Path("/config/edge") / relative_path,
            Path("/homeassistant/edge") / relative_path,
        )
        for path in paths:
            if path.exists():
                return path.read_bytes()
        raise EdgeConnectionError("Local Edge snapshot was not found.")

    async def _read_local_bytes(self, filename: str) -> bytes:
        """Read a local binary file without blocking Home Assistant's event loop."""
        if self.hass is None:
            return self._read_local_bytes_sync(filename)
        return await self.hass.async_add_executor_job(self._read_local_bytes_sync, filename)

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
            return await self._read_local_json("health.json")
        return await self._request("GET", "/health")

    async def cameras(self) -> list[dict[str, Any]]:
        """Return cameras from core API or the current add-on shell."""
        if self.is_local:
            payload = await self._read_local_json("cameras.json")
            cameras = payload.get("cameras") if isinstance(payload, dict) else payload
            config_cameras = await self._local_config_cameras()
            merged = _merge_camera_config(cameras if isinstance(cameras, list) else [], config_cameras)
            return _normalize_cameras(merged)

        try:
            payload = await self._request("GET", "/cameras")
        except EdgeConnectionError:
            payload = await self._request("GET", "/cameras.json")

        if isinstance(payload, list):
            cameras = payload
        elif isinstance(payload, dict):
            cameras = payload.get("cameras") if isinstance(payload.get("cameras"), list) else []
        else:
            cameras = []

        try:
            config_payload = await self._request("GET", "/api/config")
        except EdgeClientError:
            config_payload = {}
        config_cameras = config_payload.get("cameras") if isinstance(config_payload, dict) else []
        return _normalize_cameras(_merge_camera_config(cameras, config_cameras if isinstance(config_cameras, list) else []))

    async def _local_config_cameras(self) -> list[dict[str, Any]]:
        for filename in ("edge.json", "panel-config.json"):
            try:
                payload = await self._read_local_json(filename)
            except EdgeClientError:
                continue
            cameras = payload.get("cameras") if isinstance(payload, dict) else None
            if isinstance(cameras, list):
                return [camera for camera in cameras if isinstance(camera, dict)]
        return []

    async def camera_image(self, camera: dict[str, Any]) -> bytes | None:
        """Return the latest camera snapshot image."""
        snapshot_path = camera.get("snapshot_path") or ""
        snapshot_url = camera.get("snapshot_url") or ""

        if self.is_local:
            if not snapshot_path:
                return None
            return await self._read_local_bytes(snapshot_path)

        if not snapshot_url:
            return None

        path = snapshot_url if snapshot_url.startswith("/") else f"/{snapshot_url}"
        return await self._request("GET", path, expect_json=False)


def _hikvision_channel_from_rtsp(value: str | None, fallback: str) -> str:
    """Return the channel number from a Hikvision RTSP URL."""
    if not value or "/Streaming/Channels/" not in value:
        return fallback
    _, _, suffix = value.partition("/Streaming/Channels/")
    channel = suffix.split("/", 1)[0]
    return channel if channel.isdigit() else fallback


def _stream_rtsp(camera: dict[str, Any], stream_name: str) -> str | None:
    if stream_name == "main":
        return camera.get("rtsp_main") or camera.get("live_rtsp") or camera.get("record_rtsp")
    return camera.get("rtsp_sub") or camera.get("live_rtsp") or camera.get("record_rtsp")


def _redact_rtsp(value: str | None) -> str:
    if not value:
        return ""
    prefix = "rtsp://"
    if not value.startswith(prefix) or "@" not in value:
        return value
    auth, _, rest = value.removeprefix(prefix).partition("@")
    if ":" not in auth:
        return value
    username, _, _password = auth.partition(":")
    return f"{prefix}{username}:***@{rest}"


def _stream_profile(camera: dict[str, Any], stream_name: str) -> dict[str, Any]:
    stream_name = stream_name if stream_name in ("main", "sub") else "sub"
    fallback = HIKVISION_MAIN_CHANNEL if stream_name == "main" else HIKVISION_SUB_CHANNEL
    rtsp = _stream_rtsp(camera, stream_name) or ""
    return {
        "stream": stream_name,
        "channel": _hikvision_channel_from_rtsp(rtsp, fallback),
        "rtsp": _redact_rtsp(rtsp),
        "configured": bool(rtsp),
    }


def _camera_lookup_key(camera: dict[str, Any], index: int) -> str:
    return str(camera.get("id") or f"index:{index}")


def _merge_camera_config(status_cameras: list[dict[str, Any]], config_cameras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Overlay saved config fields onto camera status rows.

    `cameras.json` is status-oriented and can be stale for a few seconds after a save.
    The config files/API are authoritative for connection fields.
    """
    config_by_key = {
        _camera_lookup_key(camera, index): camera
        for index, camera in enumerate(config_cameras)
        if isinstance(camera, dict)
    }
    merged = []
    max_len = max(len(status_cameras), len(config_cameras))
    for index in range(max_len):
        status = status_cameras[index] if index < len(status_cameras) and isinstance(status_cameras[index], dict) else {}
        config = {}
        if index < len(config_cameras) and isinstance(config_cameras[index], dict):
            config = config_cameras[index]
        config = config_by_key.get(_camera_lookup_key(status, index), config)
        camera = dict(status)
        for field in CONFIG_CAMERA_FIELDS:
            if field in config and config[field] is not None:
                camera[field] = config[field]
        merged.append(camera)
    return merged


def _effective_streams(camera: dict[str, Any]) -> dict[str, Any]:
    live_stream = camera.get("live_stream") if camera.get("live_stream") == "main" else "sub"
    record_stream = camera.get("record_stream") if camera.get("record_stream") in ("main", "sub") else "main"
    snapshot_stream = camera.get("snapshot_stream") if camera.get("snapshot_stream") == "main" else "sub"
    tile_stream = camera.get("tile_stream") if camera.get("tile_stream") == "main" else "sub"
    return {
        "main": _stream_profile(camera, "main"),
        "sub": _stream_profile(camera, "sub"),
        "live": _stream_profile(camera, live_stream),
        "record": _stream_profile(camera, record_stream),
        "snapshot": _stream_profile(camera, snapshot_stream),
        "tile": _stream_profile(camera, tile_stream),
    }


def _normalize_camera(camera: dict[str, Any]) -> dict[str, Any]:
    """Normalize camera diagnostics before Home Assistant exposes them."""
    normalized = dict(camera)
    live_stream = normalized.get("live_stream") if normalized.get("live_stream") == "main" else "sub"
    record_stream = normalized.get("record_stream") if normalized.get("record_stream") in ("main", "sub") else "main"
    snapshot_stream = normalized.get("snapshot_stream") if normalized.get("snapshot_stream") == "main" else "sub"
    tile_stream = normalized.get("tile_stream") if normalized.get("tile_stream") == "main" else "sub"
    normalized["live_stream"] = live_stream
    normalized["record_stream"] = record_stream
    normalized["snapshot_stream"] = snapshot_stream
    normalized["tile_stream"] = tile_stream

    normalized["live_rtsp"] = _stream_rtsp(normalized, live_stream) or normalized.get("live_rtsp")
    normalized["record_rtsp"] = _stream_rtsp(normalized, record_stream) or normalized.get("record_rtsp")
    normalized["effective_streams"] = normalized.get("effective_streams") or _effective_streams(normalized)
    if normalized.get("vendor") != "hikvision":
        return normalized

    normalized["rtsp_sub_channel"] = _hikvision_channel_from_rtsp(
        normalized.get("rtsp_sub"),
        HIKVISION_SUB_CHANNEL,
    )
    normalized["hikvision_main_channel"] = _hikvision_channel_from_rtsp(
        normalized.get("rtsp_main"),
        HIKVISION_MAIN_CHANNEL,
    )
    normalized["hikvision_sub_channel"] = normalized["rtsp_sub_channel"]
    return normalized


def _normalize_cameras(cameras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _normalize_camera(camera)
        for camera in cameras
        if isinstance(camera, dict)
    ]
