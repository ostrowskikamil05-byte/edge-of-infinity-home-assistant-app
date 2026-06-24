"""Data coordinator for Edge of Infinity."""

from __future__ import annotations

import logging
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
