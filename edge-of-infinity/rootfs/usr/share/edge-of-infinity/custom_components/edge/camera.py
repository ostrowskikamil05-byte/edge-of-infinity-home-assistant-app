"""Camera platform for Edge of Infinity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import EdgeClientError
from .const import DOMAIN
from .coordinator import EdgeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Edge camera entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: EdgeCoordinator = data["coordinator"]

    async_add_entities(
        EdgeCamera(coordinator, camera)
        for camera in coordinator.data.get("cameras", [])
    )


class EdgeCamera(CoordinatorEntity[EdgeCoordinator], Camera):
    """Edge of Infinity camera entity."""

    _attr_brand = "Edge of Infinity"
    _attr_should_poll = False

    def __init__(self, coordinator: EdgeCoordinator, camera: dict[str, Any]) -> None:
        """Initialize the camera."""
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._camera_id = camera["id"]
        self._attr_unique_id = f"edge_{self._camera_id}"
        self._attr_name = camera.get("name") or self._camera_id
        self._attr_model = camera.get("vendor")

    def _camera(self) -> dict[str, Any]:
        for camera in self.coordinator.data.get("cameras", []):
            if camera.get("id") == self._camera_id:
                return camera
        return {}

    @property
    def available(self) -> bool:
        """Return if the camera is available."""
        return self._camera().get("status") == "online"

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
            return await self.coordinator.client.camera_image(self._camera())
        except EdgeClientError:
            return None


def _resolution(camera: dict[str, Any]) -> str | None:
    width = camera.get("width")
    height = camera.get("height")
    if width and height:
        return f"{width}x{height}"
    return None
