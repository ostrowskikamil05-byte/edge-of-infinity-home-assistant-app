from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_edge_panel_config import load_panel_module, camera


class NvrNavigationTests(unittest.TestCase):
    def setUp(self):
        self.p = load_panel_module()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.p.normalize_config({'storage': {'recordings_dir': str(self.root)}, 'cameras': [camera('front','192.0.2.10','main')]})
        self.p.commit_panel_config(self.config)
        self.directory = self.p.recording_base_dir(self.config['cameras'][0],0)
        self.directory.mkdir(parents=True)
        for day in ('20260727','20260728'):
            (self.directory / f'{day}-000000.mp4').write_bytes(b'x'*4096)
        self.p.MIN_RECORDING_FILE_READY_SECONDS = 0

    def test_status_measures_only_selected_day_and_keeps_empty_day(self):
        with patch.object(self.p,'recording_duration_seconds',return_value=7) as duration:
            result = self.p.recording_status_payload(self.config,{'0':'2026-07-27'})['cameras'][0]
        self.assertEqual(result['selected_day'],'2026-07-27')
        self.assertEqual(len(duration.call_args_list),1)
        self.assertIn('20260727',duration.call_args.args[0].name)
        self.assertEqual(len(result['days']),2)
        empty = self.p.recording_status_payload(self.config,{'0':'2026-07-26'})['cameras'][0]
        self.assertEqual(empty['selected_day'],'2026-07-26')
        self.assertEqual(empty['files'],[])

    def test_day_metadata_does_not_open_video_payloads(self):
        with patch.object(self.p,'load_config',return_value=self.config), patch.object(Path,'open',side_effect=AssertionError('must not open video payload')):
            entries = self.p.recording_file_entries(self.config['cameras'][0],0,limit=0,measure_durations=False)
        self.assertEqual(len(entries),2)

    def test_scrub_busy_request_does_not_queue_for_twenty_seconds(self):
        source = self.directory/'20260727-000000.mp4'
        with patch.object(self.p.shutil,'which',return_value='/usr/bin/ffmpeg'), patch.object(self.p,'RECORDING_THUMBNAIL_SEMAPHORE') as worker:
            worker.acquire.return_value=False
            with self.assertRaisesRegex(RuntimeError,'thumbnail_generation_busy'):
                self.p.ensure_recording_thumbnail('front',source,source.name,seek_seconds=5)
            worker.acquire.assert_called_once_with(timeout=.2)
