"""Integration smoke test: generated RTSP camera -> actual Edge MediaMTX -> 3 readers.

Requires FFmpeg/FFprobe and MEDIAMTX_BINARY pointing to MediaMTX 1.19.2.
Uses loopback only, ephemeral ports, temporary files; no real HA/camera.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request

from test_edge_panel_config import load_panel_module


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def until(predicate, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            result = predicate()
            if result:
                return result
        except (OSError, KeyError, ValueError):
            pass
        time.sleep(.1)
    raise AssertionError('Timed out waiting for local RTSP fixture')


def api(api_port):
    with urllib.request.urlopen(f'http://127.0.0.1:{api_port}/v3/paths/list', timeout=1) as response:
        return json.load(response)['items']


def main():
    binary = os.environ['MEDIAMTX_BINARY']
    processes = []
    logs = []
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        def run(command, name):
            log = (folder / (name + '.log')).open('wb')
            logs.append(log)
            process = subprocess.Popen(command, stdout=log, stderr=log, cwd=folder)
            processes.append(process)
            return process
        try:
            camera_port, camera_api, nvr_port, nvr_api = [port() for _ in range(4)]
            camera_config = folder/'camera.yml'
            camera_config.write_text(f'logLevel: warn\napi: yes\napiAddress: 127.0.0.1:{camera_api}\nrtspAddress: 127.0.0.1:{camera_port}\nrtspTransports: [tcp]\nmoq: no\nrtmp: no\nhls: no\nwebrtc: no\nsrt: no\npaths:\n  main:\n    source: publisher\n')
            run([binary,str(camera_config)], 'camera')
            until(lambda: isinstance(api(camera_api), list))
            run(['ffmpeg','-v','error','-re','-f','lavfi','-i','testsrc2=size=640x360:rate=10','-c:v','libx264','-threads','1','-preset','ultrafast','-tune','zerolatency','-g','10','-f','rtsp','-rtsp_transport','tcp',f'rtsp://127.0.0.1:{camera_port}/main'], 'publisher')
            until(lambda: any(row['name']=='main' and row.get('ready') for row in api(camera_api)))
            p = load_panel_module()
            p.MEDIAMTX_RTSP_PORT = nvr_port
            p.MEDIAMTX_API_PORT = nvr_api
            p.MEDIAMTX_HLS_PORT = port()
            p.MEDIAMTX_WEBRTC_PORT = port()
            p.MEDIAMTX_WEBRTC_UDP_PORT = port()
            p.MEDIAMTX_SRT_PORT = port()
            p.MEDIAMTX_RECORD = False
            config={'live': {'prebuffer_enabled': False, 'always_on_enabled': False}, 'storage': {'recordings_dir': str(folder/'recordings')}, 'cameras': [{'id':'front','enabled':True,'record':False,'low_latency':False,'rtsp_main':f'rtsp://127.0.0.1:{camera_port}/main','rtsp_sub':'','record_stream':'main'}]}
            p.write_mediamtx_runtime_config(config)
            nvr=run([binary,str(p.MEDIAMTX_CONFIG_PATH)], 'nvr')
            until(lambda: isinstance(api(nvr_api), list))
            url=p.mediamtx_rtsp_url(config['cameras'][0],0,'main')
            for index in range(3):
                run(['ffmpeg','-v','error','-rtsp_transport','tcp','-i',url,'-t','12','-c','copy','-f','null','-'], f'viewer{index}')
            until(lambda: any(row['name']=='front_main' and len(row.get('readers', []))>=3 for row in api(nvr_api)))
            upstream=next(row for row in api(camera_api) if row['name']=='main')
            assert len(upstream['readers'])==1, upstream
            result=subprocess.run(['ffprobe','-v','error','-rtsp_transport','tcp','-analyzeduration','500000','-select_streams','v:0','-show_entries','stream=width,height,codec_name','-of','json',url],capture_output=True,text=True,timeout=10,check=True)
            video=json.loads(result.stdout)['streams'][0]
            assert (video['width'],video['height'],video['codec_name'])==(640,360,'h264'),video
            assert len(next(row for row in api(camera_api) if row['name']=='main')['readers'])==1
            nvr.terminate();nvr.wait(timeout=5)
            until(lambda: len(next(row for row in api(camera_api) if row['name']=='main')['readers'])==0)
            print('PASS: actual Edge MediaMTX config, 3 simultaneous readers + probe share 1 upstream RTSP session; H264 640x360 preserved; upstream closes with NVR')
        except Exception:
            for file in folder.glob('*.log'):
                print(file.name, file.read_text(errors='replace')[-1500:])
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        process.kill();process.wait()
            for log in logs:
                log.close()


if __name__=='__main__':
    main()
