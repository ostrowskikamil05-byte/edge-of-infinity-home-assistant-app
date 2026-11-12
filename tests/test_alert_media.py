import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from test_edge_panel_config import load_panel_module, camera


class AlertMediaTests(unittest.TestCase):
    def setUp(self):
        self.p=load_panel_module()
        from edge_detection import EventStore
        from edge_recording_media import AlertMediaService, video_timing
        self.Service,self.timing=AlertMediaService,video_timing
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=EventStore(self.root/'events.sqlite')
        self.config=self.p.normalize_config({'storage':{'recordings_dir':str(self.root/'recordings')},'cameras':[camera('front','192.0.2.8','main')]})
        self.rows=[]
        self.service=self.Service(self.store,lambda:self.config,lambda *a:self.rows,lambda *a:None,lambda *a:None,start=False)

    def event(self,stamp=None,kind='person'):
        ident=self.store.add('front',kind,'local',stamp or time.time(),{})
        return self.store.get(ident)

    def sample(self,fps=20):
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg is required for actual recording media')
        source=self.root/f'video-{fps}.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'testsrc2=size=160x90:rate={fps}','-t','6.4','-c:v','libx264','-threads','1','-g',str(fps),'-tune','zerolatency','-movflags','+faststart',str(source)],check=True)
        return source

    def test_mp4_timing_reads_real_20_and_30_fps_without_camera_probe(self):
        for fps in (20,30):
            source=self.sample(fps)
            with patch.object(subprocess,'run',side_effect=AssertionError('Timing must not launch FFprobe or open camera')):
                data=self.timing(source)
            self.assertAlmostEqual(data['fps'],fps,places=4)
            self.assertAlmostEqual(data['duration'],6.4,places=2)
            self.assertAlmostEqual(data['frame_seconds'],1/fps,places=7)

    def test_saved_intrusion_remux_has_original_pixels_and_no_rtsp_input(self):
        source=self.sample();before=hashlib.sha256(source.read_bytes()).digest()
        stamp=time.time()-5
        self.rows=[{'path':source,'start_ts':stamp-3,'duration':6.4}]
        event=self.event(stamp)
        run=subprocess.run
        with patch('edge_recording_media.subprocess.run',wraps=run) as calls:
            self.service.build(event)
        saved=self.store.get(event['id']);clip=self.service.path(event['id'])
        self.assertEqual(saved['clip_state'],'ready')
        self.assertAlmostEqual(saved['clip_offset_seconds'],3)
        command=calls.call_args.args[0]
        self.assertEqual(command[command.index('-c')+1],'copy')
        self.assertFalse(any('rtsp://' in str(value) or '192.0.2' in str(value) for value in command))
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),before)
        def pixels(path):
            data=run(['ffmpeg','-v','error','-threads','1','-i',str(path),'-map','0:v:0','-threads','1','-f','framemd5','-'],capture_output=True,text=True,check=True).stdout
            return [line.rsplit(',',1)[-1] for line in data.splitlines() if line and not line.startswith('#')]
        self.assertEqual(pixels(source),pixels(clip))
        self.assertAlmostEqual(self.timing(clip)['fps'],20)

    def test_missing_coverage_never_exports_a_different_moment(self):
        source=self.root/'file.mp4';source.write_bytes(b'original')
        event=self.event();self.rows=[{'path':source,'start_ts':event['timestamp']+30,'duration':7}]
        self.service.build(event)
        self.assertEqual(self.store.get(event['id'])['media_error'],'recording_gap_or_disabled')
        self.assertFalse(self.service.path(event['id']).exists())
        self.assertEqual(source.read_bytes(),b'original')

    def test_oversized_alert_is_skipped_without_reencoding_or_deleting_original(self):
        source=self.root/'large.mp4'
        with source.open('wb') as handle:handle.truncate(40*1024*1024)
        self.config['detection']['alert_media_mb']=32
        event=self.event();self.rows=[{'path':source,'start_ts':event['timestamp']-2,'duration':7}]
        with patch('edge_recording_media.subprocess.run',side_effect=AssertionError('Must not transcode to fit quota')):
            self.service.build(event)
        self.assertEqual(self.store.get(event['id'])['media_error'],'alert_larger_than_budget')
        self.assertEqual(source.stat().st_size,40*1024*1024)

    def test_retention_prunes_alerts_but_never_originals_foreign_files_or_links(self):
        root=self.Service.directory(self.config);root.mkdir(parents=True)
        event=self.event(time.time()-15*86400);clip=self.service.path(event['id']);clip.write_bytes(b'clip')
        os.utime(clip,(event['timestamp'],event['timestamp']))
        original=self.root/'recordings/20260728-000000.mp4';original.parent.mkdir(exist_ok=True);original.write_bytes(b'original')
        foreign=root/'family.mp4';foreign.write_bytes(b'family')
        linked=root/'event-999.mp4';linked.symlink_to(original)
        self.service.prune()
        self.assertFalse(clip.exists());self.assertTrue(original.exists());self.assertTrue(foreign.exists());self.assertTrue(linked.is_symlink())

    def test_animal_points_are_exact_and_independent_of_five_minute_groups(self):
        from edge_detection import day_bounds
        start,_=day_bounds('2026-07-28')
        ids=[self.store.add('front',kind,'local',start+second,{}) for kind,second in [('person',1.25),('animal',2.5),('motion',3),('person',4.75)]]
        data=self.store.timeline('front','2026-07-28')
        self.assertEqual([point['id'] for point in data['alerts']],[ids[0],ids[1],ids[3]])
        self.assertEqual([point['day_seconds'] for point in data['alerts']],[1.25,2.5,4.75])
        self.assertEqual(len(data['markers']),1)

    def test_alert_image_and_notification_settings_stay_in_authoritative_config(self):
        config=self.p.normalize_config({'cameras':[camera('front','192.0.2.8','main')],'detection':{'animal_enabled':False,'person_enabled':True,'notifications_enabled':True,'notify_service':'notify.mobile_app_phone','alert_media_mb':64,'pre_seconds':0}})
        self.p.persist_config_payload(config)
        restored=self.p.load_config()['detection']
        self.assertIs(restored['animal_enabled'],False)
        self.assertEqual(restored['notify_service'],'notify.mobile_app_phone')
        self.assertEqual(restored['pre_seconds'],0)
        self.assertEqual(restored['alert_media_mb'],64)
        self.assertFalse(any(key.startswith('detection_') for key in self.p.addon_options_from_config(config,{})))

    def test_animal_events_do_not_open_a_camera_connection_or_repeat_active_alert(self):
        from edge_detection import DetectionService
        events=[]
        service=DetectionService(self.root/'service',lambda *a:self.fail('No camera HTTP'),lambda *a:self.fail('No new camera RTSP'),lambda *a:None,on_event=lambda event,frame:events.append(event))
        try:
            service.record('front','animal','local',True)
            service.record('front','animal','local',True)
            self.assertEqual(len(events),1)
            service.apply({'detection':{'animal_enabled':False},'cameras':[]})
            service.record('front','animal','local',True)
            self.assertEqual(len(events),1)
        finally:service.close()
