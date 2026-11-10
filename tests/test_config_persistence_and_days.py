import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from test_edge_panel_config import load_panel_module, camera, write_fake_mp4


class ConfigPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.panel = load_panel_module()
        self.config = self.panel.normalize_config({'cameras': [camera('front', '192.0.2.10', 'sub')]})
        self.panel.commit_panel_config(self.config)

    def test_settings_and_cameras_survive_stale_files_and_reload(self):
        p = self.panel
        new = {**self.config, 'storage': {**self.config['storage'], 'retention_days': 21},
               'server': {**self.config['server'], 'public_url': 'https://edge.example.test'},
               'nvr': {**self.config['nvr'], 'retention_days': 21}}
        new['cameras'] = [{**self.config['cameras'][0], 'name': 'New name', 'record_stream': 'sub'}]
        saved = p.persist_config_payload(new)[-1]
        for path in (p.PANEL_CONFIG_PATH, p.CONFIG_BACKUP_PATH, p.ADDON_CONFIG_PATH, p.PANEL_CAMERA_OVERRIDES_PATH, p.STREAM_OVERRIDES_PATH):
            p.write_json(path, self.config)
        loaded = p.load_config()
        self.assertEqual(loaded, saved)
        self.assertEqual(loaded['storage']['retention_days'], 21)
        self.assertEqual(loaded['server']['public_url'], 'https://edge.example.test')
        self.assertEqual(loaded['cameras'][0]['record_stream'], 'sub')
        self.assertEqual(p.read_json(p.PANEL_CONFIG_PATH, {}), saved)

    def test_corrupt_authoritative_file_does_not_restore_old_backup(self):
        p = self.panel
        p.write_json(p.CONFIG_BACKUP_PATH, self.config)
        p.CONFIG_PATH.write_text('{incomplete')
        with self.assertRaises(ValueError):
            p.load_config()
        self.assertEqual(p.CONFIG_PATH.read_text(), '{incomplete')

    def test_empty_existing_config_does_not_resurrect_removed_cameras(self):
        p = self.panel
        p.write_json(p.CONFIG_PATH, {'cameras': [], 'storage': {'retention_days': 30}})
        self.assertEqual(p.load_config()['cameras'], [])
        self.assertEqual(p.load_config()['storage']['retention_days'], 30)

    def test_parallel_reads_and_saves_keep_all_mirrors_consistent(self):
        p = self.panel
        def read_many():
            for _ in range(20):
                self.assertIn('cameras', p.load_config())
        def save_many():
            for retention in range(15, 24):
                p.persist_config_payload({**self.config, 'storage': {**self.config['storage'], 'retention_days': retention}})
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(read_many) for _ in range(4)] + [executor.submit(save_many)]
            for future in futures:
                future.result(timeout=15)
        expected = p.read_json(p.CONFIG_PATH, {})
        self.assertEqual(expected['storage']['retention_days'], 23)
        self.assertEqual(p.read_json(p.PANEL_CONFIG_PATH, {}), expected)
        self.assertEqual(p.read_json(p.ADDON_CONFIG_PATH, {}), expected)
        self.assertFalse(list(p.CONFIG_PATH.parent.glob('*.tmp')))

    def test_old_live_transport_options_are_removed_on_save(self):
        p = self.panel
        saved = p.persist_config_payload({**self.config, 'live': {'engine': 'mediamtx', 'mobile_webrtc_turn_url': 'turn:old.example.test'}})[-1]
        self.assertEqual(saved['live'], {'engine': 'edge_rtc'})
        self.assertEqual(p.load_config()['live'], saved['live'])


class FullDayTests(unittest.TestCase):
    def setUp(self):
        self.panel = load_panel_module()
        self.panel.MIN_RECORDING_FILE_READY_SECONDS = 0
        self.config = self.panel.normalize_config({
            'storage': {'recordings_dir': str(self.panel.HOME_DIR / 'recordings')},
            'nvr': {'segment_seconds': 10, 'playback_cache_segments': 12},
            'cameras': [camera('front', '192.0.2.10', 'sub')],
        })
        self.panel.commit_panel_config(self.config)
        self.directory = self.panel.recording_base_dir(self.config['cameras'][0], 0)
        self.directory.mkdir(parents=True)

    def test_day_list_is_not_truncated_by_cache_limit(self):
        p = self.panel
        for second in range(15):
            write_fake_mp4(self.directory / f'20260727-1200{second:02}.mp4')
        with patch.object(p, 'recording_duration_seconds', side_effect=lambda path, stat, fallback, **kwargs: fallback), patch.object(p, 'schedule_recording_cache_refresh') as build:
            status = p.recording_status_payload(self.config)['cameras'][0]
        self.assertEqual(len(status['files']), 15)
        self.assertEqual(status['timeline']['file_count'], 15)
        self.assertEqual(status['timeline']['total_seconds'], 86400)
        self.assertEqual(min(f['playback_offset'] for f in status['files']), 0)
        build.assert_not_called()

    def test_days_have_local_calendar_boundaries_including_dst(self):
        p = self.panel
        previous = os.environ.get('TZ')
        try:
            os.environ['TZ'] = 'Europe/Warsaw'
            time.tzset()
            for day, hours in [('2026-07-27', 24), ('2026-03-29', 23), ('2026-10-25', 25)]:
                start, end = p.recording_day_bounds(day)
                self.assertEqual(end - start, hours * 3600)
                self.assertEqual(time.localtime(end).tm_hour, 0)
        finally:
            if previous is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = previous
            time.tzset()

    def test_long_legacy_clip_uses_measured_duration_and_cached_probe(self):
        p = self.panel
        path = self.directory / '20260727-000000.mp4'
        write_fake_mp4(path)
        with patch.object(p, 'run_json', return_value={'format': {'duration': '420.0'}}) as probe:
            self.assertEqual(p.recording_duration_seconds(path, path.stat(), 10), 420)
            self.assertEqual(p.recording_duration_seconds(path, path.stat(), 10), 420)
            probe.assert_called_once()

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg tools required')
    def test_real_mp4_day_stream_seek_and_thumbnail(self):
        p = self.panel
        sample = self.directory / 'sample.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10',
                        '-t', '2', '-c:v', 'libx264', '-threads', '1', '-g', '10', '-pix_fmt', 'yuv420p', str(sample)], check=True, capture_output=True)
        for name in ('20260727-000000.mp4', '20260727-000002.mp4', '20260727-000004.mp4'):
            shutil.copyfile(sample, self.directory / name)
        sample.unlink()
        config = {**self.config, 'nvr': {**self.config['nvr'], 'segment_seconds': 2}}
        p.commit_panel_config(config)
        key = p.recording_key(config['cameras'][0], 0)
        plan = p.recording_stream_plan(key, 3, '2026-07-27')
        self.assertEqual(plan['first_file'], '20260727-000002.mp4')
        self.assertEqual(plan['file_count'], 2)
        self.assertEqual(plan['seek_seconds'], 1)
        output = subprocess.run(p.build_recording_stream_command(plan['concat_path'], plan['seek_seconds']), check=True, capture_output=True).stdout
        movie = self.directory / 'result.mp4'
        movie.write_bytes(output)
        probe = p.run_json(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(movie)], 4)
        self.assertGreater(float(probe['format']['duration']), 2)
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(movie), '-f', 'null', '-'], check=True, capture_output=True)
        thumb = p.ensure_recording_thumbnail(key, self.directory / '20260727-000000.mp4', '20260727-000000.mp4')
        self.assertTrue(thumb.read_bytes().startswith(b'\xff\xd8\xff'))
