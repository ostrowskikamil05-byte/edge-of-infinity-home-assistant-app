import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_edge_panel_config import load_panel_module


class RecordingRetentionTests(unittest.TestCase):
    def setUp(self):
        self.panel = load_panel_module()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 2_000_000_000

    def segment(self, relative, age):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'video')
        stamp = self.now - age * 86400
        os.utime(path, (stamp, stamp))
        return path

    def test_removes_only_expired_segments_recursively(self):
        expired = self.segment('removed_camera/20260101-000000.mp4', 15)
        legacy = self.segment('camera_main/2026-01-01_00-00-00-123456.mp4', 15)
        boundary = self.segment('camera/20260102-000000.mp4', 14)
        recent = self.segment('camera/20260103-000000.mp4', 13)
        unrelated = self.segment('family.mp4', 30)
        log = self.segment('camera/ffmpeg.log', 30)
        with patch.object(self.panel.time, 'time', return_value=self.now):
            self.assertEqual(self.panel.cleanup_old_recordings(self.root, 14), 2)
        self.assertFalse(expired.exists())
        self.assertFalse(legacy.exists())
        for path in (boundary, recent, unrelated, log):
            self.assertTrue(path.exists())

    def test_does_not_follow_symlinks(self):
        outside = self.root / 'outside'
        outside.mkdir()
        segment = self.segment('outside/20260101-000000.mp4', 30)
        recordings = self.root / 'recordings'
        recordings.mkdir()
        (recordings / 'linked_camera').symlink_to(outside, target_is_directory=True)
        (recordings / segment.name).symlink_to(segment)
        with patch.object(self.panel.time, 'time', return_value=self.now):
            self.assertEqual(self.panel.cleanup_old_recordings(recordings, 14), 0)
            self.assertEqual(self.panel.cleanup_old_recordings(recordings / 'linked_camera', 14), 0)
        self.assertTrue(segment.exists())

    def test_configuration_defaults_and_precedence(self):
        self.assertEqual(self.panel.recording_retention_days({}), 14)
        self.assertEqual(self.panel.recording_retention_days({'storage': {'retention_days': 21}}), 21)
        self.assertEqual(self.panel.recording_retention_days({'storage': {'retention_days': 21}, 'nvr': {'retention_days': 14}}), 14)
        self.assertEqual(self.panel.recording_retention_days({'storage': {'retention_days': 900}}), 365)

    def test_cleanup_does_not_require_configured_cameras(self):
        expired = self.segment('disabled_camera/20260101-000000.mp4', 15)
        config = {'storage': {'recordings_dir': str(self.root)}, 'cameras': []}
        with patch.object(self.panel.time, 'time', return_value=self.now):
            self.assertEqual(self.panel.cleanup_configured_recordings(config), 1)
        self.assertFalse(expired.exists())

    def test_background_loop_runs_immediately_and_reloads_configuration(self):
        configs = [{'nvr': {'retention_days': 14}}, {'nvr': {'retention_days': 7}}]
        with patch.object(self.panel, 'load_config', side_effect=configs), patch.object(self.panel, 'cleanup_configured_recordings') as cleanup, patch.object(self.panel.time, 'sleep', side_effect=[None, RuntimeError('stop')]) as sleep:
            with self.assertRaisesRegex(RuntimeError, 'stop'):
                self.panel.recording_retention_loop()
        self.assertEqual([call.args[0] for call in cleanup.call_args_list], configs)
        sleep.assert_called_with(3600)

    def test_background_loop_survives_cleanup_errors(self):
        with patch.object(self.panel, 'load_config', return_value={}), patch.object(self.panel, 'cleanup_configured_recordings', side_effect=OSError('disk unavailable')), patch.object(self.panel, 'write_debug_event') as log, patch.object(self.panel.time, 'sleep', side_effect=RuntimeError('stop')):
            with self.assertRaisesRegex(RuntimeError, 'stop'):
                self.panel.recording_retention_loop()
        self.assertEqual(log.call_args.args[0], 'recording_retention_error')
