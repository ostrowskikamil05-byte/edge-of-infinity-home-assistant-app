"""Data coordinator for Edge of Infinity."""

from __future__ import annotations

import logging
import math
import re
import time
from urllib.parse import quote
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
        self._entry_id = getattr(entry,'entry_id','')
        self._notify_last = {}

    async def _notify(self,event,options):
        service=str(options.get('notify_service') or '')
        if not options.get('notifications_enabled') or event.get('kind') not in ('person','animal') or not re.fullmatch(r'notify\.mobile_app_[a-z0-9_]+',service):return
        try:
            stamp=float(event.get('timestamp',0))
            cooldown=max(5,min(3600,int(options.get('notify_cooldown',30))))
        except (TypeError,ValueError,OverflowError):return
        if not math.isfinite(stamp) or abs(time.time()-stamp)>60:return
        key=(event.get('camera_id'),event.get('kind'));now=time.monotonic()
        if now-self._notify_last.get(key,-3601)<cooldown:return
        services=getattr(self.hass,'services',None)
        if services is None or not services.has_service('notify',service[7:]):return
        camera=quote(str(event['camera_id']),safe='')
        attachment=f'/api/edge/events/{self._entry_id}/{camera}/{event["id"]}/image'
        detail={'image':attachment,'tag':f'edge-{camera}-{event["kind"]}'}
        path=str(options.get('notify_path') or '')
        if path.startswith('/') and not path.startswith('//'):
            url=path+('&' if '?' in path else '?')+f'edge_event={event["id"]}'
            detail.update(url=url,clickAction=url)
        data={'title':'Edge of Infinity','message':f'{"Osoba" if event["kind"]=="person" else "Zwierzę"} · {event["camera_id"]}', 'data':detail}
        try:
            await services.async_call('notify',service[7:],data,blocking=False)
            self._notify_last[key]=now
        except Exception:
            _LOGGER.warning('Edge phone notification could not be delivered through the configured mobile app service')

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
                    await self._notify(event,detection.get('notifications') or {})
        if events:
            self._event_cursor = max(event["id"] for event in events)
        elif self._event_cursor is None:
            self._event_cursor = 0
        return {"detection": detection}
