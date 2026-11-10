"""Minimal Home Assistant stand-in for the recordings/events dashboard cards.

Runs the integration's real dashboard bridge (dashboard.py) in a bare aiohttp app,
in front of the panel fixture server. Home Assistant itself is stubbed: bearer and
signed-path authentication, entity permissions and the shared client session.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import types

from aiohttp import ClientSession, web

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / 'edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge'
PANEL = os.environ.get('EDGE_NVR_TEST_URL', 'http://127.0.0.1:18088')
PORT = int(os.environ.get('EDGE_HA_BRIDGE_PORT', '18188'))
USER_KEY = 'hass_user'
DENIED = {'camera.private'}


def load_bridge(session_factory):
    names = ('homeassistant', 'homeassistant.auth', 'homeassistant.auth.permissions', 'homeassistant.auth.permissions.const', 'homeassistant.components',
             'homeassistant.components.http', 'homeassistant.components.http.const', 'homeassistant.helpers', 'homeassistant.helpers.aiohttp_client',
             'edge_bridge_test', 'edge_bridge_test.const')
    modules = {name: types.ModuleType(name) for name in names}
    modules['homeassistant.auth.permissions.const'].POLICY_READ = 'read'
    modules['homeassistant.components.http'].HomeAssistantView = type('HomeAssistantView', (), {})
    modules['homeassistant.components.http.const'].KEY_HASS_USER = USER_KEY
    modules['homeassistant.helpers.aiohttp_client'].async_get_clientsession = session_factory
    modules['edge_bridge_test'].__path__ = [str(COMPONENT)]
    modules['edge_bridge_test.const'].DOMAIN = 'edge'
    sys.modules.update(modules)
    spec = importlib.util.spec_from_file_location('edge_bridge_test.dashboard', COMPONENT / 'dashboard.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_hass(panel=PANEL):
    entities = {
        'front:nvr': {'camera_id': 'front', 'role': 'nvr', 'entity_id': 'camera.front', 'name': 'Front'},
        'back:nvr': {'camera_id': 'back', 'role': 'nvr', 'entity_id': 'camera.back', 'name': 'Back'},
        'front:main': {'camera_id': 'front', 'role': 'main', 'entity_id': 'camera.private', 'name': 'Front · Main'},
    }
    entry = {'client': types.SimpleNamespace(is_local=True, base_url='local', api_key=None), 'entities': entities,
             'coordinator': types.SimpleNamespace(data={'cameras': [{'id': 'front', 'edge_rtc': {'keyframe_api': panel + '/api/edge-rtc/keyframe/front'}}]})}
    return types.SimpleNamespace(data={'edge': {'entry': entry}}, http=types.SimpleNamespace(register_view=lambda view: None))


PAGE = """<!doctype html><html lang="pl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Edge dashboard cards fixture</title>
<style>html{--primary-text-color:#e6edf3;--secondary-text-color:#9fb0bf;--card-background-color:#1c2530;--secondary-background-color:#27323f;--divider-color:#334150;--primary-color:#03a9f4}
body{margin:0;padding:12px;background:#111820;font-family:system-ui,sans-serif}main{display:grid;gap:12px;grid-template-columns:repeat(4,minmax(0,1fr));max-width:1500px;margin:auto}
main>*{grid-column:1/-1;min-width:0}@media(min-width:1000px){main.split>edge-events-card{grid-column:4}main.split>edge-recordings-card{grid-column:1/4;grid-row:1}}</style>
<main></main><script src="/edge/edge-dashboard-cards.js"></script><script>
const query=new URLSearchParams(location.search);window.fixture={signed:[],api:[]};
const camera=(id,name)=>({state:'idle',attributes:{friendly_name:name,edge_camera_id:id,edge_stream_role:'nvr'}});
window.hass={locale:{language:query.get('lang')||'pl'},states:{'camera.front':camera('front','Front'),'camera.back':camera('back','Ogród'),
  'camera.private':{state:'idle',attributes:{friendly_name:'Front · Main',edge_camera_id:'front',edge_stream_role:'main'}},
  'binary_sensor.front_person':{state:'off',attributes:{edge_camera_id:'front'}}},
 async callApi(method,path){fixture.api.push(path);const r=await fetch('/api/'+path,{headers:{Authorization:'Bearer fixture'}});const text=await r.text();let body=text;try{body=JSON.parse(text)}catch(_){}
  if(!r.ok)throw {error:'Request error',status_code:r.status,body};return body;},
 fetchWithAuth(path,init={}){return fetch(path,{...init,headers:{...(init.headers||{}),Authorization:'Bearer fixture'}});},
 async callWS(message){if(message.type!=='auth/sign_path')throw new Error('Unexpected WS '+message.type);fixture.signed.push(message.path);return {path:message.path+'?authSig='+encodeURIComponent('signed:'+message.path)};}};
const main=document.querySelector('main');const cards=(query.get('cards')||'recordings,events').split(',');if(query.get('split'))main.className='split';
for(const name of cards){const card=document.createElement('edge-'+name+'-card');const config=JSON.parse(query.get(name)||'{}');card.setConfig({type:'custom:edge-'+name+'-card',...config});card.hass=window.hass;main.append(card);}
</script></html>"""


def create_app(panel=PANEL):
    state = {}
    bridge = load_bridge(lambda hass: state['session'])
    hass = build_hass(panel)
    user = types.SimpleNamespace(permissions=types.SimpleNamespace(check_entity=lambda entity_id, policy: entity_id not in DENIED))

    @web.middleware
    async def authenticate(request, handler):
        if request.path.startswith('/api/'):
            signed = request.query.get('authSig') == 'signed:' + request.path
            if request.headers.get('Authorization') != 'Bearer fixture' and not signed:
                return web.json_response({'error': 'unauthorized'}, status=401)
            request[USER_KEY] = user
        return await handler(request)

    async def start(app):
        state['session'] = ClientSession()

    async def stop(app):
        await state['session'].close()

    app = web.Application(middlewares=[authenticate])
    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    for view_class in (bridge.EdgeRecordingsView, bridge.EdgeEventsView, bridge.EdgeMediaView):
        view = view_class(hass)
        # Home Assistant passes URL parameters to the view as keyword arguments.
        app.router.add_get(view.url, lambda request, view=view: view.get(request, **request.match_info))
    app.router.add_get('/', lambda request: web.Response(text=PAGE, content_type='text/html'))
    app.router.add_get('/api/version', lambda request: web.json_response({'bridge': True}))
    app.router.add_static('/edge', COMPONENT / 'www')
    app['hass'] = hass
    return app


if __name__ == '__main__':
    print(json.dumps({'bridge': f'http://127.0.0.1:{PORT}', 'panel': PANEL}), flush=True)
    web.run_app(create_app(), host='127.0.0.1', port=PORT, print=None)
