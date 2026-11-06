"""Real EdgeRTC camera/core + actual HA bridge code, with minimal HA API stubs.

Run only with generated test media. Never contacts a physical camera or real HA.
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlparse
import types

from aiohttp import ClientSession, web

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT/'edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www'

async def main():
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        video = folder/'camera.h264'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=10','-t','1','-c:v','libx264','-threads','1','-preset','ultrafast','-tune','zerolatency','-profile:v','baseline','-g','10','-x264-params','aud=1:repeat-headers=1','-f','h264',str(video)],check=True)
        info_file = folder/'info.json'
        log = (folder/'fixture.log').open('wb')
        process = subprocess.Popen([os.environ['EDGE_RTC_TEST_BINARY'],'-test.run=^TestBrowserFixture$','-test.v'],env={**os.environ,'EDGE_RTC_TEST_VIDEO':str(video),'EDGE_RTC_TEST_INFO':str(info_file)},stdout=log,stderr=log)
        try:
            for _ in range(150):
                if info_file.exists():
                    break
                if process.poll() is not None:
                    raise RuntimeError((folder/'fixture.log').read_text())
                await asyncio.sleep(.1)
            info=json.loads(info_file.read_text())
            # Import the real integration WebSocket bridge without installing HA Core.
            names=['homeassistant','homeassistant.auth','homeassistant.auth.permissions','homeassistant.auth.permissions.const','homeassistant.components','homeassistant.components.http','homeassistant.components.http.const','homeassistant.helpers','homeassistant.helpers.aiohttp_client','edge_fixture','edge_fixture.const','edge_fixture.client']
            for name in names:
                sys.modules[name]=types.ModuleType(name)
            sys.modules['homeassistant.auth.permissions.const'].POLICY_READ='read'
            sys.modules['homeassistant.auth.permissions.const'].POLICY_CONTROL='control'
            sys.modules['homeassistant.components.http'].HomeAssistantView=object
            sys.modules['homeassistant.components.http.const'].KEY_HASS_USER='hass_user'
            sys.modules['edge_fixture.const'].DOMAIN='edge'
            sys.modules['edge_fixture.client'].EdgeClientError=type('EdgeClientError',(Exception,),{})
            async with ClientSession() as session:
                sys.modules['homeassistant.helpers.aiohttp_client'].async_get_clientsession=lambda hass:session
                spec=importlib.util.spec_from_file_location('edge_fixture.rtc',ASSETS.parent/'rtc.py')
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                async def token(name):
                    return info['token'].encode()
                client=types.SimpleNamespace(is_local=True,_read_local_bytes=token)
                ids=['camera.front','camera.side','camera.back','camera.garage']
                rows=[{'camera_id':str(index),'entity_id':entity,'role':'nvr','name':entity} for index,entity in enumerate(ids)]
                cameras=[{'id':str(index),'enabled':True,'edge_rtc':{'api':info['api'],'path':'front_main'}} for index in range(4)]
                hass=types.SimpleNamespace(data={'edge':{'entry':{'client':client,'coordinator':types.SimpleNamespace(data={'cameras':cameras}),'entities':{str(i):row for i,row in enumerate(rows)}}}})
                bridge=module.EdgeRTCView(hass)
                async def websocket(request):
                    allowed = request.query.get('deny')!='1'
                    request['hass_user']=types.SimpleNamespace(permissions=types.SimpleNamespace(check_entity=lambda entity,policy: allowed or policy=='read'))
                    return await bridge.get(request,request.match_info['entity'])
                async def status(request):
                    async with session.get(info['api']+'/api/status',headers={'Authorization':'Bearer '+info['token']}) as response:
                        return web.json_response(await response.json())
                async def index(request):
                    return web.Response(text='<html><body style="margin:8px;background:#0d151e;--primary-text-color:#edf5f7;--card-background-color:#111d28"></body></html>',content_type='text/html')
                async def asset(request):
                    name=request.match_info['name']
                    if name not in ['edge-live-card.js','edge-rtc-client.js','edge-pcm-worklet.js']:
                        raise web.HTTPNotFound()
                    return web.Response(body=(ASSETS/name).read_bytes(),content_type='text/javascript')
                sys.path.insert(0, str(ROOT/'tests'))
                from test_edge_panel_config import load_panel_module
                panel=load_panel_module();panel.EDGE_RTC_ENABLED=True;panel.MEDIAMTX_ENABLED=False;panel.MEDIAMTX_API_PORT=urlparse(info['api']).port
                panel.HOME_DIR.mkdir(parents=True,exist_ok=True);(panel.HOME_DIR/'edge-rtc-token').write_text(info['token'])
                server=panel.ThreadingHTTPServer(('127.0.0.1',18090),panel.EdgeHandler);threading.Thread(target=server.serve_forever,daemon=True).start()
                async def record(request):
                    command=panel.build_recording_command(info['rtsp'],str(folder/'record-%Y%m%d-%H%M%S.mp4'),2,'copy')
                    with (folder/'recorder.log').open('wb') as record_log:
                        recorder=await asyncio.create_subprocess_exec(*command,stdout=record_log,stderr=record_log)
                        await asyncio.sleep(3.5);recorder.terminate();await recorder.wait()
                    files=[]
                    for file in folder.glob('record-*.mp4'):
                        result=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=codec_name,width,height','-of','json',str(file)],capture_output=True,text=True)
                        if result.returncode==0:
                            files.extend(json.loads(result.stdout).get('streams',[]))
                    return web.json_response({'files':files,'copy_video':command[command.index('-c:v')+1]=='copy'})
                app=web.Application();app.router.add_get('/',index);app.router.add_get('/edge/{name}',asset);app.router.add_get('/api/edge/rtc/{entity}',websocket);app.router.add_get('/test/status',status);app.router.add_get('/test/record',record)
                runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',18089);await site.start()
                print('EdgeRTC real transport fixture ready on 18089',flush=True)
                event=asyncio.Event();asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,event.set);await event.wait();await runner.cleanup();await asyncio.to_thread(server.shutdown);server.server_close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill();process.wait()
            log.close()
            print((folder/'fixture.log').read_text()[-2500:],flush=True)

asyncio.run(main())
