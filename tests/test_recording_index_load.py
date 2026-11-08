import gzip
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_edge_panel_config import load_panel_module,camera


class RecordingIndexLoadTests(unittest.TestCase):
    def setUp(self):
        self.p=load_panel_module()
        self.config=self.p.normalize_config({'storage':{'recordings_dir':str(self.p.HOME_DIR/'recordings')},'cameras':[camera('front','192.0.2.8','main')]})
        self.p.commit_panel_config(self.config)

    def test_summary_never_scans_archive_or_reports_history_as_empty(self):
        with patch.object(self.p,'recording_file_candidates',side_effect=AssertionError('summary must not scan')):
            data=self.p.recording_status_response(self.config,{'summary':['1']})
        self.assertTrue(data['summary']);self.assertTrue(data['cameras'][0]['desired_recording'])
        self.assertNotIn('files',data['cameras'][0]);self.assertNotIn('days',data['cameras'][0])

    def test_large_index_uses_one_metadata_pass_and_no_probes_storage_scan_or_disabled_cache_signature(self):
        p=self.p;directory=p.recording_base_dir(self.config['cameras'][0],0);directory.mkdir(parents=True)
        start=p.recording_day_start_ts('2026-11-07');rows=[]
        for i in range(12000):
            stamp=start+i*10;name=p.time.strftime('%Y%m%d-%H%M%S.mp4',p.time.localtime(stamp))
            stat=SimpleNamespace(st_mtime=stamp,st_mtime_ns=stamp*1000000000,st_size=3000000)
            rows.append((directory/name,stat,stamp))
        with patch.object(p,'recording_file_candidates',return_value=(rows,len(rows))) as scan,\
             patch.object(p,'mp4_duration_seconds',return_value=10) as duration,\
             patch.object(p,'run_json',side_effect=AssertionError('No external probe in a listing')),\
             patch.object(p,'storage_usage_payload',side_effect=AssertionError('Storage walk must not block playback')),\
             patch.object(p,'recording_cache_source_signature',side_effect=AssertionError('Disabled cache must not stat/hash sources')):
            first=p.recording_status_payload(self.config,{'0':'2026-11-07'})
            self.assertEqual(scan.call_count,1);self.assertEqual(len(first['cameras'][0]['files']),8640)
            self.assertEqual(duration.call_count,8640)
            second=p.recording_status_payload(self.config,{'0':'2026-11-07'})
            self.assertEqual(duration.call_count,8640,'Warm requests must reuse MP4 durations')
            self.assertEqual(second['cameras'][0]['files_revision'],first['cameras'][0]['files_revision'])
            self.assertTrue(first['storage']['pending']);self.assertGreater(first['storage']['recordings_bytes'],0)

    def test_bad_movie_falls_back_without_starting_ffprobe_and_caches_the_failure(self):
        path=self.p.HOME_DIR/'broken.mp4';path.parent.mkdir(exist_ok=True);path.write_bytes(b'broken')
        stat=path.stat()
        with patch.object(self.p,'run_json',side_effect=AssertionError('listing probe')),patch.object(self.p,'mp4_duration_seconds',return_value=0) as read:
            self.assertEqual(self.p.recording_duration_seconds(path,stat,24,allow_probe=False),24)
            self.assertEqual(self.p.recording_duration_seconds(path,stat,24,allow_probe=False),24)
            self.assertEqual(read.call_count,1)

    def test_legacy_h264_choice_migrates_only_recording_policy_and_keeps_backup(self):
        raw={**self.config,'nvr':{**self.config['nvr'],'browser_playback':'h264'},'unknown_user_field':'keep'}
        self.p.write_json(self.p.CONFIG_PATH,raw)
        self.assertTrue(self.p.migrate_recording_policy());self.assertFalse(self.p.migrate_recording_policy())
        saved=self.p.read_json(self.p.CONFIG_PATH,{})
        self.assertEqual(saved['cameras'],raw['cameras']);self.assertEqual(saved['storage'],raw['storage'])
        self.assertEqual(saved['unknown_user_field'],'keep');self.assertEqual(saved['nvr']['browser_playback'],'copy')
        self.assertEqual(self.p.read_json(self.p.CONFIG_BACKUP_PATH,{}),raw)

    def test_gzip_index_roundtrip_and_opt_out_keep_exact_json_values(self):
        payload={'cameras':[{'files':[{'name':f'{i}.mp4','url':f'recordings/front/{i}.mp4','start_ts':1794000000+i} for i in range(12000)]}]}
        for encoding,compressed in [('gzip, deflate, br',True),('gzip;q=0.8',True),('gzip;q=0',False),('',False)]:
            handler=self.p.EdgeHandler.__new__(self.p.EdgeHandler);handler.headers={'Accept-Encoding':encoding}
            with patch.object(handler,'send_bytes') as send:
                handler.send_json(payload,compress=True)
            body,content,status,headers=send.call_args.args
            self.assertEqual(headers.get('Content-Encoding')=='gzip',compressed)
            self.assertEqual(json.loads(gzip.decompress(body) if compressed else body),payload)
            if compressed:self.assertLess(len(body),len(json.dumps(payload).encode())/5)

    def test_rtsp_redaction_cannot_swallow_a_multiline_ffmpeg_command(self):
        text='"rtsp://127.0.0.1:8556/front_main",\n"-c:v", "copy"\n[hevc @ 0x123] Error constructing RPS'
        self.assertEqual(self.p.redact_rtsp(text),text)
        self.assertNotIn('secret',self.p.redact_rtsp('rtsp://admin:secret@192.0.2.8/main'))
