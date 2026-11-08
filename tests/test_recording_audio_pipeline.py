import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_edge_panel_config import load_panel_module


class RecordingAudioPipelineTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
        self.p=load_panel_module()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def run_media(self,args):
        return subprocess.run(['ffmpeg','-hide_banner','-loglevel','warning','-nostdin','-y',*args],check=True,capture_output=True)

    def sample(self,audio='pcm_mulaw',duration=2):
        source=self.root/(audio+'.mov')
        self.run_media(['-f','lavfi','-i','testsrc2=size=160x90:rate=20','-f','lavfi','-i','sine=sample_rate=8000',
                        '-t',str(duration),'-c:v','libx264','-threads','1','-g','20','-c:a',audio,'-ar','8000',str(source)])
        return source

    def frames(self,path):
        result=self.run_media(['-threads','1','-i',str(path),'-map','0:v','-threads','1','-f','framemd5','-'])
        return [row.rsplit(b',',1)[-1] for row in result.stdout.splitlines() if row and not row.startswith(b'#')]

    def record(self,source,codec):
        pattern=self.root/'recorded-%Y%m%d-%H%M%S.mp4'
        command=self.p.build_recording_command('rtsp://shared/source',str(pattern),10,'copy','h264',codec,8000,1)
        result=self.run_media(['-threads','1','-i',str(source),*command[command.index('-map'):]])
        return next(self.root.glob('recorded-*.mp4')),result

    def test_both_g711_inputs_encode_voice_aac_once_and_preserve_every_video_frame(self):
        for codec in ('pcm_alaw','pcm_mulaw'):
            with self.subTest(codec=codec):
                source=self.sample(codec);before=hashlib.sha256(source.read_bytes()).digest()
                target,result=self.record(source,codec)
                info=self.p.video_timing(target)
                self.assertEqual(info['audio_codec'],'mp4a');self.assertEqual(info['audio_sample_rate'],16000)
                self.assertTrue(info['audio_clock_regular']);self.assertEqual(info['fps'],20)
                self.assertEqual(self.frames(source),self.frames(target))
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),before)
                self.assertNotIn(b'Non-monotonic',result.stderr);self.assertNotIn(b'backward in time',result.stderr)
                target.unlink()

    def test_native_aac_recording_preserves_encoded_audio_packets(self):
        source=self.sample('aac');target,_=self.record(source,'aac')
        def packets(path):return self.run_media(['-i',str(path),'-map','0:a','-c:a','copy','-f','adts','-']).stdout
        self.assertEqual(packets(source),packets(target))
        self.assertEqual(self.frames(source),self.frames(target))

    def test_small_real_b_frame_and_aac_edits_bypass_an_unrelated_worker(self):
        source=self.sample();target,_=self.record(source,'pcm_mulaw')
        from edge_recording_compat import CompatibleRecordings
        service=CompatibleRecordings(self.root/'cache',budget=1)
        info=self.p.video_timing(target)
        self.assertGreater(info['video_start_seconds'],0)
        self.assertLessEqual(info['video_start_seconds'],.15)
        self.assertGreater(info['audio_start_seconds'],0)
        with service.lock:
            self.assertEqual(service.prepare(target,allow_passthrough=True,allow_small_offset=True),target)
        self.assertFalse(service.directory.exists())

    def test_old_audio_tail_is_trimmed_without_shortening_or_encoding_video(self):
        source=self.sample();long=self.root/'long-audio.mp4'
        self.run_media(['-i',str(source),'-f','lavfi','-i','sine=sample_rate=16000',
                        '-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-t','5','-movflags','+faststart',str(long)])
        from edge_recording_compat import CompatibleRecordings
        self.assertGreater(self.p.video_timing(long)['audio_duration_seconds'],4.5)
        service=CompatibleRecordings(self.root/'cache')
        legacy=service.prepare(long,legacy=True)
        self.assertGreater(self.p.video_timing(legacy)['audio_duration_seconds'],4.5)
        target=service.prepare(long,allow_passthrough=True)
        self.assertNotEqual(target,legacy,'Old tabs must retain the previous byte representation')
        info=self.p.video_timing(target)
        self.assertLessEqual(info['audio_duration_seconds']-info['duration'],.15)
        self.assertTrue(info['audio_clock_regular']);self.assertEqual(self.frames(source),self.frames(target))

