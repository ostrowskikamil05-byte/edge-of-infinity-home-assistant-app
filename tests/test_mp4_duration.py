from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_edge_panel_config import load_panel_module, camera


def box(kind, payload, extended=False):
    if extended:
        return (1).to_bytes(4, 'big') + kind + (16 + len(payload)).to_bytes(8, 'big') + payload
    return (8 + len(payload)).to_bytes(4, 'big') + kind + payload


def movie(duration, version=0):
    wide = version == 1
    data = bytes([version, 0, 0, 0]) + bytes(16 if wide else 8) + (1000).to_bytes(4, 'big') + int(duration * 1000).to_bytes(8 if wide else 4, 'big')
    return box(b'ftyp', b'isom' + bytes(12)) + box(b'mdat', bytes(2048)) + box(b'moov', box(b'mvhd', data), extended=wide)


class Mp4DurationTests(unittest.TestCase):
    def setUp(self):
        self.panel = load_panel_module()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.mp4'

    def test_reads_movie_duration_with_moov_after_video_data(self):
        self.path.write_bytes(movie(7.25))
        self.assertEqual(self.panel.mp4_duration_seconds(self.path), 7.25)

    def test_reads_version_one_and_extended_box_size(self):
        self.path.write_bytes(movie(420, version=1))
        self.assertEqual(self.panel.mp4_duration_seconds(self.path), 420)

    def test_truncated_and_invalid_headers_do_not_produce_duration(self):
        for data in (b'', b'broken', movie(7)[:-4], box(b'moov', b'bad header'), box(b'moov', box(b'mvhd', bytes(32)))):
            self.path.write_bytes(data)
            self.assertEqual(self.panel.mp4_duration_seconds(self.path), 0)

    def test_day_duration_is_video_length_instead_of_filename_gaps(self):
        p = self.panel
        p.MIN_RECORDING_FILE_READY_SECONDS = 0
        config = p.normalize_config({'cameras': [camera('front', '192.0.2.10', 'sub')], 'storage': {'recordings_dir': str(Path(self.temp.name) / 'recordings')}})
        p.commit_panel_config(config)
        directory = p.recording_base_dir(config['cameras'][0], 0)
        directory.mkdir(parents=True)
        for name in ('20260727-000000.mp4', '20260727-000010.mp4'):
            (directory / name).write_bytes(movie(7))
        with patch.object(p, 'run_json') as probe:
            entries = p.recording_file_entries(config['cameras'][0], 0, limit=0, segment_seconds=10)
        self.assertEqual([entry['duration_seconds'] for entry in entries], [7, 7])
        self.assertEqual([entry['playback_offset'] for entry in entries], [0, 7])
        probe.assert_not_called()
