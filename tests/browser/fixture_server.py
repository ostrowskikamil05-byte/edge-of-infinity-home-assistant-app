"""Isolated browser-test server using generated MP4s; never uses HA or real cameras."""
import sys
import json
import os
import shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_edge_panel_config import load_panel_module, camera
p=load_panel_module()
p.MIN_RECORDING_FILE_READY_SECONDS=0
config=p.normalize_config({'storage': {'recordings_dir': str(p.HOME_DIR/'recordings')}, 'nvr': {'segment_seconds': 7}, 'cameras': [camera('front', '192.0.2.10', 'sub')]})
p.commit_panel_config(config)
p.sync_runtime_engine_config=lambda *a: {}
p.schedule_recording_ensure=lambda *a: None
p.schedule_recording_cache_refresh=lambda *a,**kw: False
p.refresh_status=lambda: {}
p.remember_camera_presets=lambda *a: None
p.write_json(p.DATA_DIR/'cameras.json', {'cameras': []})
d=p.recording_base_dir(config['cameras'][0],0); d.mkdir(parents=True)
import subprocess
sample=d/'sample.mp4'
subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=10','-t','7','-c:v','libx264','-threads','1','-g','10',str(sample)],check=True)
for name in ['20260727-000000.mp4','20260727-000007.mp4','20260727-000014.mp4','20260728-000000.mp4','20260728-000007.mp4','20260728-000014.mp4']:
 shutil.copyfile(sample,d/name)
sample.unlink()
Path(os.environ.get('EDGE_NVR_FIXTURE_INFO', '/tmp/edge-nvr-browser-fixture.json')).write_text(json.dumps({'recordings_dir': str(d)}))
p.ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('EDGE_NVR_TEST_PORT', '18088'))),p.EdgeHandler).serve_forever()
