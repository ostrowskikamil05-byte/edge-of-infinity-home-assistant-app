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
import time
from urllib.parse import urlparse
import types

from aiohttp import ClientSession, web

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT/'edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www'

async def main():
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        hevc = os.environ.get('EDGE_RTC_TEST_CODEC') == 'h265'
        fps = max(1, min(60, int(os.environ.get('EDGE_RTC_TEST_FPS', '10'))))
        gop = max(1, min(120, int(os.environ.get('EDGE_RTC_TEST_GOP_SECONDS', '1'))))
        video = folder/('camera.hevc' if hevc else 'camera.h264')
        encoding = ['-c:v','libx265','-x265-params','aud=1:repeat-headers=1:pools=1:frame-threads=1:keyint=10:bframes=0:log-level=error','-f','hevc'] if hevc else ['-c:v','libx264','-threads','1','-preset','ultrafast','-tune','zerolatency','-profile:v','baseline','-g','10','-x264-params','aud=1:repeat-headers=1','-f','h264']
        if not hevc and gop > 1:
            encoding[encoding.index('-g')+1]=str(fps*gop)
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'testsrc2=size=640x360:rate={fps}','-t',str(gop),*encoding,str(video)],check=True)
        archive = os.environ.get('EDGE_RTC_TEST_ARCHIVE') == '1'
        if archive:
            saved=folder/'recordings/front';saved.mkdir(parents=True)
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'testsrc2=size=640x360:rate={fps}','-f','lavfi','-i','sine=sample_rate=48000','-t','12',*encoding[:-2],'-c:a','aac','-threads:a','1','-movflags','+faststart',str(saved/'20260728-000000.mp4')],check=True)
        info_file = folder/'info.json'
        log = (folder/'fixture.log').open('wb')
        process = subprocess.Popen([os.environ['EDGE_RTC_TEST_BINARY'],'-test.run=^TestBrowserFixture$','-test.v'],env={**os.environ,'EDGE_RTC_TEST_VIDEO':str(video),'EDGE_RTC_TEST_INFO':str(info_file),**({'EDGE_RTC_TEST_RECORDINGS':str(folder/'recordings')} if archive else {})},stdout=log,stderr=log)
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
            sys.modules['edge_fixture.const'].CARD_VERSION='0.17.12'
            sys.modules['edge_fixture.client'].EdgeClientError=type('EdgeClientError',(Exception,),{})
            async with ClientSession() as session:
                sys.modules['homeassistant.helpers.aiohttp_client'].async_get_clientsession=lambda hass:session
                spec=importlib.util.spec_from_file_location('edge_fixture.rtc',ASSETS.parent/'rtc.py')
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                async def token(name):
                    return info['token'].encode()
                client=types.SimpleNamespace(is_local=True,base_url='local',_read_local_bytes=token)
                ids=['camera.front','camera.side','camera.back','camera.garage']
                rows=[{'camera_id':str(index),'entity_id':entity,'role':'nvr','name':entity} for index,entity in enumerate(ids)]
                cameras=[{'id':str(index),'enabled':True,'edge_rtc':{'api':info['api'],'path':'front_main'}} for index in range(4)]
                hass=types.SimpleNamespace(data={'edge':{'entry':{'client':client,'coordinator':types.SimpleNamespace(data={'cameras':cameras}),'entities':{str(i):row for i,row in enumerate(rows)}}}})
                if archive:
                    rows[0]['camera_id']='front';cameras[0].update(id='front',index=0,key='front_0')
                    cameras[0]['edge_rtc']['keyframe_api']='http://127.0.0.1:18090/api/edge-rtc/keyframe/front'
                bridge=module.EdgeRTCView(hass)
                spec=importlib.util.spec_from_file_location('edge_fixture.device_diagnostics',ASSETS.parent/'device_diagnostics.py')
                diagnostics_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(diagnostics_module)
                async def report_device(payload,peer):return await asyncio.to_thread(panel.DEVICE_LOGS.append,payload,peer)
                client.report_device=report_device
                device_bridge=diagnostics_module.EdgeDeviceDiagnosticsView(hass)
                async def device_report(request):
                    request['hass_user']=types.SimpleNamespace(permissions=types.SimpleNamespace(check_entity=lambda entity,policy:request.query.get('deny')!='1'))
                    return await device_bridge.post(request)
                async def device_logs(request):return web.json_response(panel.DEVICE_LOGS.snapshot(request.query.get('device','')))
                async def websocket(request):
                    allowed = request.query.get('deny')!='1'
                    request['hass_user']=types.SimpleNamespace(permissions=types.SimpleNamespace(check_entity=lambda entity,policy: allowed or policy=='read'))
                    return await bridge.get(request,request.match_info['entity'])
                async def status(request):
                    async with session.get(info['api']+'/api/status',headers={'Authorization':'Bearer '+info['token']}) as response:
                        return web.json_response(await response.json())
                async def index(request):
                    return web.Response(text='<html><meta name="viewport" content="width=device-width,initial-scale=1"><body style="margin:8px;background:#0d151e;--primary-text-color:#edf5f7;--card-background-color:#111d28"></body></html>',content_type='text/html')
                async def asset(request):
                    name=request.match_info['name']
                    if name not in ['edge-live-card.js','edge-rtc-client.js','edge-pcm-worklet.js','edge-device-diagnostics.js','edge-i18n.js','edge-brand.png','edge-dashboard-cards.js','edge-dvr-client.js','edge-recording-gallery.js']:
                        raise web.HTTPNotFound()
                    return web.Response(body=(ASSETS/name).read_bytes(),content_type='image/png' if name.endswith('.png') else 'text/javascript')
                sys.path.insert(0, str(ROOT/'tests'))
                from test_edge_panel_config import load_panel_module
                os.environ['EDGE_CORE_ENGINE']='edge_rtc'
                panel=load_panel_module();panel.EDGE_RTC_ENABLED=True;panel.EDGE_RTC_API_PORT=urlparse(info['api']).port
                panel.HOME_DIR.mkdir(parents=True,exist_ok=True);(panel.HOME_DIR/'edge-rtc-token').write_text(info['token'])
                if archive:
                    from test_edge_panel_config import camera as make_camera
                    panel.MIN_RECORDING_FILE_READY_SECONDS=0
                    panel.commit_panel_config(panel.normalize_config({'storage':{'recordings_dir':str(folder/'recordings')},'cameras':[make_camera('front','192.0.2.10','main')]}))
                    panel.schedule_recording_ensure=lambda *args:None
                    panel.schedule_recording_cache_refresh=lambda *args,**kw:False
                    panel.sync_runtime_engine_config=lambda *args:{}
                    spec=importlib.util.spec_from_file_location('edge_fixture.dashboard',ASSETS.parent/'dashboard.py')
                    dashboard=importlib.util.module_from_spec(spec);spec.loader.exec_module(dashboard)
                server=panel.ThreadingHTTPServer(('127.0.0.1',18090),panel.EdgeHandler);threading.Thread(target=server.serve_forever,daemon=True).start()
                from edge_detection import DetectionService
                from test_edge_panel_config import camera
                detector = None
                if os.environ.get('EDGE_RTC_TEST_DETECTION'):
                    detector = DetectionService(panel.HOME_DIR, lambda *args: None, lambda *args: info['rtsp'], panel.write_json)
                    detector.apply({'detection':{'local_enabled':True},'cameras':[{**camera('front','192.0.2.10','sub'),'local_detection_enabled':True}]})
                    panel.DETECTION_SERVICE = detector
                async def detection_status(request):
                    return web.json_response(detector.snapshot() if detector else {})
                async def record(request):
                    metadata=panel.recording_registry_metadata(camera('front','192.0.2.10','main'),0,'main')
                    mode,video_codec,audio_codec,probe=panel.recording_codec_mode(info['rtsp'],{},metadata)
                    audio_info=probe.get('audio') or {}
                    command=panel.build_recording_command(info['rtsp'],str(folder/'record-%Y%m%d-%H%M%S.mp4'),2,mode,video_codec,audio_codec,audio_info.get('sample_rate'),audio_info.get('channels'))
                    with (folder/'recorder.log').open('wb') as record_log:
                        recorder=await asyncio.create_subprocess_exec(*command,stdout=record_log,stderr=record_log)
                        # Wait for recorded media, not for a fixed time: recorder start-up
                        # varies with machine load, and a fixed window left too little
                        # (or nothing) to export on a slow runner. A second file means
                        # the first two-second segment is complete.
                        deadline=time.monotonic()+30;first_seen=None
                        while time.monotonic()<deadline and recorder.returncode is None:
                            segments=[path for path in folder.glob('record-*.mp4') if path.stat().st_size>0]
                            if segments and first_seen is None:first_seen=time.monotonic()
                            if len(segments)>=2 or (first_seen is not None and time.monotonic()-first_seen>6):break
                            await asyncio.sleep(.1)
                        await asyncio.sleep(.5)
                        if recorder.returncode is None:recorder.terminate()
                        await recorder.wait()
                    def hashes(path):
                        result=subprocess.run(['ffmpeg','-v','error','-threads','1','-i',str(path),'-map','0:v:0','-threads','1','-f','framemd5','-'],capture_output=True,text=True,check=True)
                        return [line.rsplit(',',1)[-1].strip() for line in result.stdout.splitlines() if line and not line.startswith('#')]
                    original_hashes=set(hashes(video))
                    files=[]
                    pixel_identical=True
                    for file in folder.glob('record-*.mp4'):
                        result=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=codec_name,codec_tag_string,width,height,r_frame_rate','-of','json',str(file)],capture_output=True,text=True)
                        if result.returncode==0:
                            files.extend(json.loads(result.stdout).get('streams',[]))
                            recorded_hashes=hashes(file)
                            pixel_identical=pixel_identical and bool(recorded_hashes) and set(recorded_hashes).issubset(original_hashes)
                    return web.json_response({'files':files,'copy_video':command[command.index('-c:v')+1]=='copy','pixel_identical':pixel_identical})
                async def alert(request):
                    # Export already-recorded HEVC while live, inference and talk
                    # remain attached. The exporter receives no camera address.
                    def build():
                        from edge_detection import EventStore
                        from edge_recording_media import AlertMediaService, video_timing
                        store=EventStore(folder/'alert-events.sqlite')
                        rows=[];stamp=time.time()-20
                        for path in sorted(folder.glob('record-*.mp4')):
                            duration=video_timing(path)['duration']
                            if duration>0:
                                rows.append({'path':path,'start_ts':stamp,'duration':duration});stamp+=duration
                        if not rows:raise RuntimeError('no finished recording segment to export')
                        ident=store.add('front','animal','local',rows[0]['start_ts']+min(.5,rows[0]['duration']/2),{})
                        service=AlertMediaService(store,lambda:{'storage':{'recordings_dir':str(folder/'alerts')},'detection':{}},lambda *args:rows,lambda *args:None,lambda *args:None,start=False)
                        service.build(store.get(ident));clip=service.path(ident)
                        def pixels(path):
                            output=subprocess.run(['ffmpeg','-v','error','-threads','1','-i',str(path),'-map','0:v:0','-threads','1','-f','framemd5','-'],capture_output=True,text=True,check=True).stdout
                            return [line.rsplit(',',1)[-1].strip() for line in output.splitlines() if line and not line.startswith('#')]
                        original=[value for row in rows for value in pixels(row['path'])]
                        recorded_fps=sum(video_timing(row['path'])['fps']*row['duration'] for row in rows)/sum(row['duration'] for row in rows)
                        return {'state':store.get(ident)['clip_state'],'pixel_identical':original==pixels(clip),'fps':video_timing(clip)['fps'],'recorded_fps':recorded_fps}
                    # Report why the export failed instead of an opaque HTTP 500.
                    try:return web.json_response(await asyncio.to_thread(build))
                    except Exception as error:
                        recorded=[{'name':path.name,'bytes':path.stat().st_size} for path in sorted(folder.glob('record-*.mp4'))]
                        return web.json_response({'state':'error','error':type(error).__name__+': '+str(error)[:400],'recorded':recorded})
                app=web.Application();app.router.add_get('/',index);app.router.add_get('/edge/{name}',asset);app.router.add_get('/api/edge/rtc/{entity}',websocket);app.router.add_post('/api/edge/client-diagnostics',device_report);app.router.add_get('/test/devices',device_logs);app.router.add_get('/test/status',status);app.router.add_get('/test/detection',detection_status);app.router.add_get('/test/record',record);app.router.add_get('/test/alert',alert)
                if archive:
                    for view_class in (dashboard.EdgeRecordingsView,dashboard.EdgeEventsView,dashboard.EdgeMediaView,dashboard.EdgeArchiveView,dashboard.EdgeDashboardStatusView):
                        view=view_class(hass)
                        async def handle(request,view=view):
                            request['hass_user']=types.SimpleNamespace(permissions=types.SimpleNamespace(check_entity=lambda entity,policy:True))
                            return await view.get(request,**request.match_info)
                        app.router.add_get(view.url,handle)
                runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',18089);await site.start()
                print('EdgeRTC real transport fixture ready on 18089',flush=True)
                event=asyncio.Event();asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,event.set);await event.wait();await runner.cleanup();await asyncio.to_thread(server.shutdown);server.server_close()
        finally:
            if 'detector' in locals() and detector:
                detector.close()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill();process.wait()
            log.close()
            print((folder/'fixture.log').read_text()[-2500:],flush=True)

asyncio.run(main())
