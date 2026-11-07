"""Data coordinator for Edge of Infinity."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import EdgeClient, EdgeClientError
from .const import DEFAULT_SCAN_INTERVAL_SECONDS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class EdgeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch camera data from Edge."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EdgeClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            health = await self.client.health()
            cameras = await self.client.cameras()
        except EdgeClientError as err:
            raise UpdateFailed(str(err)) from err

        return {
            "health": health,
            "cameras": cameras,
        }



class EdgeDetectionCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Read event metadata frequently without re-polling camera/health state."""
    def __init__(self, hass, client, entry):
        super().__init__(hass, logger=_LOGGER, config_entry=entry, name=DOMAIN + " detection", update_interval=timedelta(seconds=2))
        self.client = client
        self._event_cursor = None

    async def _async_update_data(self):
        detection = await self.client.detection()
        if not detection:
            return {"detection": {}}
        if detection:
            try:
                if time.time() - float(detection.get("updated_at", 0)) > 15:
                    raise UpdateFailed("Edge detection status is stale")
            except (TypeError, ValueError) as error:
                raise UpdateFailed("Invalid Edge detection status") from error
        events = sorted((event for event in detection.get("recent_events", []) if isinstance(event, dict) and isinstance(event.get("id"), int)), key=lambda event: event["id"])
        if self._event_cursor is not None:
            for event in events:
                if event["id"] > self._event_cursor:
                    self.hass.bus.async_fire("edge_event", {key: event[key] for key in ("id", "camera_id", "kind", "source", "timestamp", "score", "day", "day_seconds", "zone_id") if key in event})
        if events:
            self._event_cursor = max(event["id"] for event in events)
        elif self._event_cursor is None:
            self._event_cursor = 0
        return {"detection": detection}
