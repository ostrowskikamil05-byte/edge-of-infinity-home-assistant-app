"""Authenticated HA WebSocket bridge for the native EdgeRTC/1 frame protocol."""
import asyncio
from contextlib import suppress
import re

from aiohttp import ClientError, WSMsgType, web
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .client import EdgeClientError


class EdgeRTCView(HomeAssistantView):
    url = '/api/edge/rtc/{entity_id}'
    name = 'api:edge:rtc'
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, entity_id):
        hass = self.hass
        user = request[KEY_HASS_USER]
        microphone = request.query.get('microphone') == '1'
        if not re.fullmatch(r'camera\.[a-z0-9_]+', entity_id):
            return web.Response(status=400)
        if not user.permissions.check_entity(entity_id, POLICY_READ) or (microphone and not user.permissions.check_entity(entity_id, POLICY_CONTROL)):
            return web.Response(status=403)
        target = None
        for entry in hass.data.get(DOMAIN, {}).values():
            if not isinstance(entry, dict) or not entry.get('coordinator'):
                continue
            for row in entry.get('entities', {}).values():
                if row.get('entity_id') == entity_id:
                    camera = next((c for c in entry['coordinator'].data.get('cameras', []) if c.get('id') == row.get('camera_id')), None)
                    if camera and camera.get('enabled') and camera.get('edge_rtc'):
                        target = (entry['client'], camera['edge_rtc'])
        if target is None:
            return web.Response(status=404)
        client, route = target
        try:
            token = (await client._read_local_bytes('edge-rtc-token')).decode().strip() if client.is_local else client.api_key
        except (EdgeClientError, OSError, UnicodeError):
            return web.Response(status=503)
        if not token:
            return web.Response(status=503)
        endpoint = route['api'].replace('http://', 'ws://', 1).replace('https://', 'wss://', 1) + '/api/stream/' + route['path'] + ('?microphone=1' if microphone else '')
        front = web.WebSocketResponse(heartbeat=20, compress=False, max_msg_size=8*1024*1024)
        if not front.can_prepare(request).ok:return web.Response(status=400)
        await front.prepare(request)
        async def synchronize():
            try:
                async with async_get_clientsession(hass).post(route['keyframe_api'],headers={'Authorization':'Bearer '+token},timeout=2) as response:
                    await response.read()
            except (ClientError,OSError,TimeoutError):pass
        signal=asyncio.create_task(synchronize()) if route.get('keyframe_api') else None
        try:
            async with async_get_clientsession(hass).ws_connect(endpoint, headers={'Authorization': 'Bearer ' + token}, max_msg_size=8*1024*1024, heartbeat=20) as back:
                async def forward(source, destination, upstream=False):
                    async for message in source:
                        if message.type == WSMsgType.BINARY:
                            if upstream and (not microphone or len(message.data) != 321 or message.data[0] != 3):
                                break
                            await destination.send_bytes(message.data)
                        elif message.type == WSMsgType.TEXT:
                            if upstream:
                                break
                            await destination.send_str(message.data)
                        else:
                            break
                tasks = [asyncio.create_task(forward(front, back, True)), asyncio.create_task(forward(back, front))]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (ClientError, OSError, TimeoutError):
            with suppress(ConnectionError):
                await front.send_json({'type': 'error', 'code': 'edge_rtc_unavailable'})
        finally:
            if signal:
                signal.cancel()
                await asyncio.gather(signal,return_exceptions=True)
            await front.close()
        return front


def async_register_rtc(hass):
    hass.http.register_view(EdgeRTCView(hass))
