import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_edge_panel_config import load_panel_module


class RecordingCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.p=load_panel_module()
        from edge_recording_compat import CompatibleRecordings
        from edge_recording_media import video_timing
        self.Service,self.timing=CompatibleRecordings,video_timing
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.cache=self.Service(self.root/'cache')

    def sample(self, codec='libx264'):
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
        path=self.root/f'{codec}.mp4'
        command=['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=20',
                 '-t','1.5','-c:v',codec,'-threads','1']
        if codec=='libx265':command+=['-x265-params','pools=none:frame-threads=1:log-level=error','-tag:v','hev1']
        subprocess.run([*command,'-movflags','+faststart',str(path)],check=True,capture_output=True)
        return path

    def test_mobile_h264_and_legacy_hevc_preserve_all_pixels_fps_and_originals(self):
        for codec,entry in [('libx264','avc1'),('libx265','hvc1')]:
            source=self.sample(codec);before=hashlib.sha256(source.read_bytes()).digest()
            copy=self.cache.prepare(source)
            self.assertEqual(self.timing(copy)['sample_entry'],entry)
            self.assertEqual(self.timing(copy)['fps'],20)
            self.assertEqual(self.timing(copy)['duration'],self.timing(source)['duration'])
            def frames(path):
                result=subprocess.run(['ffmpeg','-v','error','-threads','1','-i',str(path),'-threads','1','-f','framemd5','-'],capture_output=True,text=True,check=True)
                return [line.rsplit(',',1)[-1] for line in result.stdout.splitlines() if line and not line.startswith('#')]
            self.assertEqual(frames(source),frames(copy))
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),before)
            with patch('edge_recording_compat.subprocess.run',side_effect=AssertionError('Range requests must reuse the cached remux')):
                self.assertEqual(self.cache.prepare(source),copy)

    def test_cache_budget_and_retention_preserve_originals_foreign_files_and_links(self):
        source=self.sample();cache=self.Service(self.root/'cache',budget=3*1024*1024,max_files=1)
        first=cache.prepare(source)
        foreign=first.parent/'family.mp4';foreign.write_bytes(b'keep')
        linked=first.parent/('a'*64+'.mp4');linked.symlink_to(source)
        other=self.root/'other.mp4';shutil.copyfile(source,other)
        second=cache.prepare(other)
        self.assertFalse(first.exists());self.assertTrue(second.exists())
        self.assertTrue(foreign.exists());self.assertTrue(linked.is_symlink());self.assertTrue(source.exists())
        self.assertLessEqual(sum(p.stat().st_size for p in second.parent.glob('*.mp4') if not p.is_symlink() and len(p.stem)==64),cache.budget)
        with patch('edge_recording_compat.subprocess.run',side_effect=AssertionError('Over budget must never transcode')):
            with self.assertRaisesRegex(RuntimeError,'budget'):self.Service(self.root/'small',budget=1).prepare(source)

    def test_recording_uses_source_timestamps_and_hvc1_without_transcoding(self):
        command=self.p.build_recording_command('rtsp://127.0.0.1:8554/front','test-%Y.mp4',10,'copy','hevc')
        self.assertNotIn('-use_wallclock_as_timestamps',command)
        self.assertEqual(command[command.index('-tag:v')+1],'hvc1')
        self.assertEqual(command[command.index('-c:v')+1],'copy')

    def test_cache_rejects_symlink_and_removes_failed_temporary(self):
        source=self.sample();link=self.root/'link.mp4';link.symlink_to(source)
        with self.assertRaisesRegex(RuntimeError,'invalid_recording_source'):self.cache.prepare(link)
        with patch('edge_recording_compat.subprocess.run',side_effect=subprocess.TimeoutExpired('ffmpeg',20)):
            with self.assertRaises(subprocess.TimeoutExpired):self.cache.prepare(source)
        self.assertFalse((self.cache.directory/'remux.tmp').exists());self.assertTrue(source.exists())
