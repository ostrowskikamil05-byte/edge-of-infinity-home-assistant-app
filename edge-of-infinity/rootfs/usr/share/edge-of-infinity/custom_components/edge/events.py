"""Authenticated alert pictures for Home Assistant Companion notifications."""
import asyncio
import re

from aiohttp import web
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.const import KEY_HASS_USER

from .client import EdgeClientError
from .const import DOMAIN


class EdgeEventImageView(HomeAssistantView):
    url='/api/edge/events/{entry_id}/{camera_id}/{event_id}/image'
    name='api:edge:event_image'
    requires_auth=True

    def __init__(self,hass):self.hass=hass

    async def get(self,request,entry_id,camera_id,event_id):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',camera_id) or not re.fullmatch(r'[1-9][0-9]{0,18}',event_id):
            return web.Response(status=400)
        if int(event_id)>9223372036854775807:return web.Response(status=400)
        entry=self.hass.data.get(DOMAIN,{}).get(entry_id)
        if not isinstance(entry,dict) or not entry.get('client'):return web.Response(status=404)
        user=request[KEY_HASS_USER]
        allowed=any(row.get('camera_id')==camera_id and user.permissions.check_entity(row.get('entity_id',''),POLICY_READ) for row in entry.get('entities',{}).values())
        if not allowed:return web.Response(status=403)
        for attempt in range(10):
            try:
                data=await entry['client'].event_image(camera_id,int(event_id))
                return web.Response(body=data,content_type='image/jpeg',headers={'Cache-Control':'private, max-age=60'})
            except (EdgeClientError,OSError,ValueError):
                if attempt==9:return web.Response(status=404)
                await asyncio.sleep(.15)
