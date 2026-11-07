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
fixture_cameras=[camera('front','192.0.2.10','sub')]
if os.environ.get('EDGE_TEST_MULTI_CAMERA'):fixture_cameras.append(camera('back','192.0.2.11','main'))
config=p.normalize_config({'storage': {'recordings_dir': str(p.HOME_DIR/'recordings')}, 'nvr': {'segment_seconds': 7}, 'cameras': fixture_cameras})
p.commit_panel_config(config)
p.sync_runtime_engine_config=lambda *a: {}
p.sync_supervisor_options=lambda *a: {"synced": True, "status": "synced"}
p.schedule_recording_ensure=lambda *a: None
p.schedule_recording_cache_refresh=lambda *a,**kw: False
p.refresh_status=lambda: {}
p.remember_camera_presets=lambda *a: None
p.write_json(p.DATA_DIR/'cameras.json', {'cameras': []})
if os.environ.get('EDGE_TEST_MULTI_CAMERA'):
 p.write_json(p.DATA_DIR/'cameras.json',{'cameras':[{**c,'index':i,'key':p.recording_key(c,i),'status':'online','video_codec':'hevc' if i==0 else 'h264'} for i,c in enumerate(config['cameras'])]})
if os.environ.get('EDGE_TEST_DETECTION_FIXTURE'):
 from edge_detection import DetectionService
 from datetime import datetime
 def fixture_soap(*args):
  raise RuntimeError('No physical ONVIF camera in this fixture')
 p.DETECTION_SERVICE=DetectionService(p.HOME_DIR,fixture_soap,lambda *args:'',p.write_json)
 p.DETECTION_SERVICE.store.prune=lambda *args:None
 p.DETECTION_SERVICE.apply(config)
 # Fixed one-frame JPEG for browser tests; no camera or network access.
 import subprocess, time
 preview_jpeg=subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=416x234:rate=1','-frames:v','1','-c:v','mjpeg','-threads','1','-f','image2pipe','pipe:1'],capture_output=True,check=True).stdout
 def fixture_preview(camera_id):
  if not any(c['id']==camera_id and c['enabled'] for c in p.load_config()['cameras']):
   raise KeyError('camera_unavailable')
  return preview_jpeg,416,234,time.time()
 p.DETECTION_SERVICE.preview=fixture_preview
 p.DETECTION_SERVICE.store.add('front','person','local',datetime(2026,7,27,0,0,8).timestamp(),{'score':.91})
 p.DETECTION_SERVICE.store.add('front','vehicle','camera',datetime(2026,7,28,0,0,15).timestamp(),{})
 for index in range(210):
  p.DETECTION_SERVICE.store.add('front','motion','camera',datetime(2026,7,28,0,1).timestamp()+index*300,{})
 # Keep historical paging IDs/counts stable while adding one intrusion to today.
 with p.DETECTION_SERVICE.store.connect() as db:
  db.execute("UPDATE edge_events SET kind='animal',timestamp=? WHERE id=3",(datetime(2026,7,28,0,0,6).timestamp(),))
 fixture_alert_ids=[1,3]
 if os.environ.get('EDGE_TEST_MULTI_CAMERA'):
  fixture_alert_ids.append(p.DETECTION_SERVICE.store.add('back','person','local',datetime(2026,7,28,0,0,9).timestamp(),{'score':.95}))
 for ident in fixture_alert_ids:
  event=p.DETECTION_SERVICE.store.get(ident)
  image=p.alert_image_path(ident);image.parent.mkdir(parents=True,exist_ok=True);image.write_bytes(preview_jpeg)
  p.write_json(image.with_suffix('.json'),{key:event[key] for key in ('id','camera_id','kind','timestamp')})
  p.DETECTION_SERVICE.store.update_media(ident,has_image=True,clip_state='pending')
d=p.recording_base_dir(config['cameras'][0],0); d.mkdir(parents=True)
import subprocess
sample=d/'sample.mp4'
subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=10','-t',os.environ.get('EDGE_TEST_RECORDING_DURATION','7'),'-c:v','libx264','-threads','1','-g','10',str(sample)],check=True)
for name in ['20260727-000000.mp4','20260727-000007.mp4','20260727-000014.mp4','20260728-000000.mp4','20260728-000007.mp4','20260728-000014.mp4']:
 shutil.copyfile(sample,d/name)
sample.unlink()
if os.environ.get('EDGE_TEST_MULTI_CAMERA'):
 for i,c in enumerate(config['cameras']):
  folder=p.recording_base_dir(c,i);folder.mkdir(exist_ok=True,parents=True)
  sample=folder/'sample.mp4'
  encoding=['-c:v','libx265','-x265-params','pools=none:frame-threads=1:log-level=error','-tag:v','hev1'] if i==0 else ['-c:v','libx264','-threads','1','-g','20']
  subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=20','-f','lavfi','-i','sine=sample_rate=48000',
                  '-t','7',*encoding,'-c:a','aac','-movflags','+faststart',str(sample)],check=True)
  for day in ('20260727','20260728'):
   for second in (0,7,14):shutil.copyfile(sample,folder/f'{day}-0000{second:02d}.mp4')
  sample.unlink()
if os.environ.get('EDGE_TEST_DETECTION_FIXTURE'):
 p.ALERT_SERVICE=p.AlertMediaService(p.DETECTION_SERVICE.store,p.load_config,p.alert_source_entries,p.save_alert_image,p.save_alert_fallback_image,start=False)
 # Fixture dates are intentionally historical; retention is covered separately.
 p.ALERT_SERVICE.prune=lambda reserve=0:{'bytes':0,'limit_bytes':256*1024*1024}
 for ident in fixture_alert_ids:p.ALERT_SERVICE.build(p.DETECTION_SERVICE.store.get(ident))
Path(os.environ.get('EDGE_NVR_FIXTURE_INFO', '/tmp/edge-nvr-browser-fixture.json')).write_text(json.dumps({'recordings_dir': str(d), 'recording_cache_dir':str(p.RECORDING_CACHE_DIR), 'thumbnail_dir':str(p.RECORDING_THUMB_DIR)}))
p.ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('EDGE_NVR_TEST_PORT', '18088'))),p.EdgeHandler).serve_forever()
