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
    modules['edge_bridge_test.const'].CARD_VERSION = '0.17.12'
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
    info_path=os.environ.get('EDGE_NVR_FIXTURE_INFO')
    if info_path:
        info=json.loads(Path(info_path).read_text())
        async def read_token(name):
            if name!='edge-rtc-token':raise ValueError('unexpected local file')
            return info['archive_token'].encode()
        entry['client']._read_local_bytes=read_token
        entry['coordinator'].data['cameras']=[{'id':name,'index':index,'key':name+'_'+str(index),'edge_rtc':{'api':info['archive_core'],'keyframe_api':panel+'/api/edge-rtc/keyframe/'+name}} for index,name in enumerate(('front','back'))]
    return types.SimpleNamespace(data={'edge': {'entry': entry}}, http=types.SimpleNamespace(register_view=lambda view: None))


PAGE = """<!doctype html><html lang="pl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Edge dashboard cards fixture</title>
<style>html{--primary-text-color:#e6edf3;--secondary-text-color:#9fb0bf;--card-background-color:#1c2530;--secondary-background-color:#27323f;--divider-color:#334150;--primary-color:#03a9f4}
body{margin:0;padding:12px;background:#111820;font-family:system-ui,sans-serif}main{display:grid;gap:12px;grid-template-columns:repeat(4,minmax(0,1fr));max-width:1500px;margin:auto}
main>*{grid-column:1/-1;min-width:0}@media(min-width:1000px){main.split>edge-events-card{grid-column:4}main.split>edge-recordings-card{grid-column:1/4;grid-row:1}}</style>
<main></main><script>
// Like Home Assistant's own ha-card: the frame comes only from theme variables.
customElements.define('ha-card',class extends HTMLElement{constructor(){super();this.attachShadow({mode:'open'}).innerHTML='<style>:host{display:block;background:var(--ha-card-background,var(--card-background-color,#fff));-webkit-backdrop-filter:var(--ha-card-backdrop-filter,none);backdrop-filter:var(--ha-card-backdrop-filter,none);box-shadow:var(--ha-card-box-shadow,none);border-radius:var(--ha-card-border-radius,12px);border:var(--ha-card-border-width,1px) solid var(--ha-card-border-color,var(--divider-color,#e0e0e0));color:var(--primary-text-color)}</style><slot></slot>';}});
const THEMES={glass:{'--ha-card-background':'rgba(255,255,255,0.10)','--ha-card-backdrop-filter':'blur(22px)','--ha-card-border-width':'0px','--ha-card-border-radius':'24px','--ha-card-box-shadow':'0 8px 32px rgba(0,0,0,.18)','--primary-text-color':'#ffffff','--secondary-text-color':'rgba(255,255,255,.7)','--primary-color':'#ffd08a','--card-background-color':'#3b3550',page:'linear-gradient(135deg,#3a4d8f,#8a4f9e 50%,#d9825b)'},
 light:{'--ha-card-background':'#ffffff','--card-background-color':'#ffffff','--ha-card-border-radius':'12px','--primary-text-color':'#1c1c1e','--secondary-text-color':'#6b7280','--primary-color':'#0b7bd3','--divider-color':'#e0e0e0',page:'#f2f3f5'}};
{const theme=THEMES[new URLSearchParams(location.search).get('theme')];if(theme)for(const [name,value] of Object.entries(theme)){if(name==='page'){document.documentElement.style.background=value;document.documentElement.style.minHeight='100%';document.body.style.background='transparent';}else document.documentElement.style.setProperty(name,value);}}
</script><script src="/edge/edge-dashboard-cards.js"></script><script>
const query=new URLSearchParams(location.search);window.fixture={signed:[],api:[]};
// ?integration=x.y.z: the integration code Home Assistant is running (camera entities report it).
const camera=(id,name)=>({state:'idle',attributes:{friendly_name:name,edge_camera_id:id,edge_stream_role:'nvr',...(query.get('integration')?{edge_live_card_version:query.get('integration')}:{})}});
window.hass={user:{id:'fixture-dashboard-user'},locale:{language:query.get('lang')||'pl'},states:{'camera.front':camera('front','Front'),'camera.back':camera('back','Ogród'),
  'camera.private':{state:'idle',attributes:{friendly_name:'Front · Main',edge_camera_id:'front',edge_stream_role:'main'}},
  'binary_sensor.front_person':{state:'off',attributes:{edge_camera_id:'front'}}},
 async callApi(method,path){fixture.api.push(path);const r=await fetch('/api/'+path,{headers:{Authorization:'Bearer fixture'}});const text=await r.text();let body=text;try{body=JSON.parse(text)}catch(_){}
  if(!r.ok)throw {error:'Request error',status_code:r.status,body};return body;},
 fetchWithAuth(path,init={}){return fetch(path,{...init,headers:{...(init.headers||{}),Authorization:'Bearer fixture'}});},
 async callWS(message){if(message.type!=='auth/sign_path')throw new Error('Unexpected WS '+message.type);fixture.signed.push(message.path);return {path:message.path+(message.path.includes('?')?'&':'?')+'authSig='+encodeURIComponent('signed:'+message.path)};}};
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
            signed_path=request.path+('?'+'&'.join(key+'='+value for key,value in request.query.items() if key!='authSig') if any(key!='authSig' for key in request.query) else '')
            signed = request.query.get('authSig') == 'signed:' + signed_path
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
    for view_class in (bridge.EdgeRecordingsView, bridge.EdgeEventsView, bridge.EdgeMediaView, bridge.EdgeArchiveView, bridge.EdgeDashboardStatusView):
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
