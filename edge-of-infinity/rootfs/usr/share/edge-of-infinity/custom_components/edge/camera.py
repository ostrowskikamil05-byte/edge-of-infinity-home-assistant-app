"""Camera platform for Edge of Infinity."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import EdgeClientError, camera_stream_source
from .const import DOMAIN
from .coordinator import EdgeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Edge camera entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: EdgeCoordinator = data["coordinator"]

    known: set[str] = set()

    def add_new_cameras() -> None:
        cameras = []
        for camera in coordinator.data.get("cameras", []):
            camera_id = camera.get("id")
            if not camera_id or camera_id in known:
                continue
            known.add(camera_id)
            cameras.append(EdgeCamera(coordinator, camera))
        if cameras:
            async_add_entities(cameras)

    add_new_cameras()
    entry.async_on_unload(coordinator.async_add_listener(add_new_cameras))


async def publish_entity_map(hass: HomeAssistant) -> None:
    """Publish only camera/entity IDs to the shared config directory."""
    domain_data = hass.data[DOMAIN]
    lock = domain_data.setdefault("_entity_map_lock", asyncio.Lock())
    async with lock:
        entities = []
        for data in domain_data.values():
            if not isinstance(data, dict):
                continue
            client = data.get("client")
            for camera_id, entity in data.get("entities", {}).items():
                entities.append({"camera_id": camera_id, **entity, "local": bool(client and client.is_local)})
        payload = {"entities": entities}
        path = Path(hass.config.path("edge", "ha-entities.json"))

        def write_map() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                    tmp = Path(handle.name)
                    json.dump(payload, handle)
                tmp.replace(path)
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)

        await hass.async_add_executor_job(write_map)


class EdgeCamera(CoordinatorEntity[EdgeCoordinator], Camera):
    """Edge of Infinity camera entity."""

    _attr_brand = "Edge of Infinity"
    _attr_should_poll = False
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: EdgeCoordinator, camera: dict[str, Any]) -> None:
        """Initialize the camera."""
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._camera_id = camera["id"]
        self._attr_unique_id = f"edge_{self._camera_id}"
        self._attr_name = camera.get("name") or self._camera_id
        self._attr_model = camera.get("vendor")
        self._last_stream_source = camera_stream_source(camera)
        self.stream_options = {"rtsp_transport": "tcp"}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._publish_entity()

    async def _publish_entity(self) -> None:
        data = self.hass.data.get(DOMAIN, {}).get(self.coordinator.config_entry.entry_id)
        if not isinstance(data, dict) or not self.entity_id:
            return
        data.setdefault("entities", {})[self._camera_id] = {"entity_id": self.entity_id, "name": self._attr_name}
        try:
            await publish_entity_map(self.hass)
        except OSError:
            _LOGGER.warning("Could not publish Edge camera entity IDs for the card builder")

    async def async_will_remove_from_hass(self) -> None:
        data = self.hass.data[DOMAIN].get(self.coordinator.config_entry.entry_id, {})
        data.get("entities", {}).pop(self._camera_id, None)
        try:
            await publish_entity_map(self.hass)
        except OSError:
            _LOGGER.warning("Could not publish Edge camera entity IDs for the card builder")
        await super().async_will_remove_from_hass()

    async def stream_source(self) -> str | None:
        """Provide the source to the authenticated Home Assistant stream pipeline."""
        return camera_stream_source(self._camera())

    def _handle_coordinator_update(self) -> None:
        camera = self._camera()
        source = camera_stream_source(camera)
        if self.stream and source and source != self._last_stream_source:
            self.stream.update_source(source)
        self._last_stream_source = source
        self._attr_name = camera.get("name") or self._camera_id
        self._attr_model = camera.get("vendor")
        data = self.hass.data[DOMAIN].get(self.coordinator.config_entry.entry_id, {})
        mapped = data.get("entities", {}).get(self._camera_id) or {}
        if mapped.get("entity_id") != self.entity_id or mapped.get("name") != self._attr_name:
            self.hass.async_create_task(self._publish_entity())
        super()._handle_coordinator_update()

    def _camera(self) -> dict[str, Any]:
        for camera in self.coordinator.data.get("cameras", []):
            if camera.get("id") == self._camera_id:
                return camera
        return {}

    @property
    def available(self) -> bool:
        """Return if the camera is available."""
        camera = self._camera()
        return bool(camera.get("enabled", True) and camera.get("status") == "online" and getattr(self.coordinator, "last_update_success", True))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return Edge diagnostic attributes."""
        camera = self._camera()
        return {
            "edge_camera_id": self._camera_id,
            "vendor": camera.get("vendor"),
            "host": camera.get("host"),
            "status": camera.get("status"),
            "detail": camera.get("detail"),
            "codec": camera.get("codec"),
            "video_codec": camera.get("video_codec"),
            "audio_codec": camera.get("audio_codec"),
            "audio_sample_rate": camera.get("audio_sample_rate"),
            "audio_channels": camera.get("audio_channels"),
            "resolution": _resolution(camera),
            "fps": camera.get("fps"),
            "snapshot_stream": camera.get("snapshot_stream"),
            "live_stream": camera.get("live_stream"),
            "tile_stream": camera.get("tile_stream"),
            "record_stream": camera.get("record_stream"),
            "rtsp_sub_channel": camera.get("rtsp_sub_channel"),
            "hikvision_main_channel": camera.get("hikvision_main_channel"),
            "hikvision_sub_channel": camera.get("hikvision_sub_channel"),
            "snapshot_path": camera.get("snapshot_path"),
            "snapshot_url": camera.get("snapshot_url"),
            "low_latency": camera.get("low_latency"),
            "record": camera.get("record"),
        }

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return camera snapshot image."""
        try:
            image = await self.coordinator.client.camera_image(self._camera())
            if image:
                return image
        except EdgeClientError:
            pass
        source = camera_stream_source(self._camera(), "snapshot_stream")
        if source is None:
            return None
        try:
            return await async_get_image(self.hass, source, width=width, height=height)
        except (OSError, RuntimeError, TimeoutError):
            return None



def _resolution(camera: dict[str, Any]) -> str | None:
    width = camera.get("width")
    height = camera.get("height")
    if width and height:
        return f"{width}x{height}"
    return None

