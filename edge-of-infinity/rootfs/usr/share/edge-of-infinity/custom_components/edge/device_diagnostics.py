"""Authenticated, read-permission checked device telemetry from Edge cards."""
import re
from aiohttp import web
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.const import KEY_HASS_USER
from .const import DOMAIN
from .client import EdgeClientError


class EdgeDeviceDiagnosticsView(HomeAssistantView):
    url='/api/edge/client-diagnostics'
    name='api:edge:client-diagnostics'
    requires_auth=True

    def __init__(self,hass):self.hass=hass

    async def post(self,request):
        if request.content_length is None or request.content_length>32768:return web.Response(status=413)
        try:
            data=await request.json();entity=data.pop('entity_id','') if isinstance(data,dict) else ''
            if not re.fullmatch(r'camera\.[a-z0-9_]+',entity):return web.Response(status=400)
            if not request[KEY_HASS_USER].permissions.check_entity(entity,POLICY_READ):return web.Response(status=403)
            for entry in self.hass.data.get(DOMAIN,{}).values():
                if not isinstance(entry,dict) or not entry.get('client'):continue
                if any(row.get('entity_id')==entity for row in entry.get('entities',{}).values()):
                    accepted=await entry['client'].report_device(data,{'bridge':'authenticated_home_assistant','ha_observed_peer':request.remote,'entity_id':entity})
                    return web.json_response({'ok':accepted},status=200 if accepted else 429)
            return web.Response(status=404)
        except (ValueError,TypeError):return web.Response(status=400)
        except (OSError,EdgeClientError):return web.Response(status=503)
