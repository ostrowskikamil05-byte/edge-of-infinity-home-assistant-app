import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from test_edge_panel_config import load_panel_module, camera


class RecordingStorageTests(unittest.TestCase):
    def setUp(self):
        self.p = load_panel_module()
        import edge_storage
        self.s = edge_storage
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.p.normalize_config({'storage':{'recordings_dir':str(self.root/'originals')},'cameras':[camera('front','192.0.2.8','main')]})
        self.p.commit_panel_config(self.config)

    def file(self, path, size=4096, age=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x'*size)
        stamp = time.time()-age
        os.utime(path,(stamp,stamp))
        return path

    def test_default_and_legacy_auto_keep_hevc_and_native_frame_rate(self):
        for policy in ({},{'browser_playback':'auto_h264'},{'browser_playback':'copy'},{'browser_playback':'h264'}):
            with self.subTest(policy=policy):
                normalized = self.p.normalize_config({'nvr':policy})['nvr']
                self.assertEqual(normalized['browser_playback'],'copy')
                self.assertEqual(self.p.recording_mode_from_codec('hevc',policy),'copy')
                with patch.object(self.p,'probe_rtsp_stream',return_value={'video':{'codec_name':'hevc'}}):
                    mode,*_ = self.p.recording_codec_mode('rtsp://shared',policy)
                command = self.p.build_recording_command('rtsp://shared','%Y%m%d-%H%M%S.mp4',10,mode)
                self.assertEqual(command[command.index('-c:v')+1],'copy')
                self.assertNotIn('-r',command)
                self.assertNotIn('-vf',command)
        self.assertEqual(self.p.recording_mode_from_codec('hevc',{'browser_playback':'h264'}),'copy')

    def test_disabled_day_cache_never_starts_or_scans_background_video(self):
        self.assertEqual(self.p.recording_cache_budget(self.config),0)
        with patch.object(self.p,'recording_file_entries',side_effect=AssertionError('unnecessary scan')):
            self.p.refresh_recording_caches(self.config,'test')
        with patch.object(self.p.threading,'Thread') as worker:
            self.assertFalse(self.p.schedule_recording_cache_refresh('front_0',[],10))
            worker.assert_not_called()

    def test_cleanup_removes_only_derived_video_and_orphan_thumbnails(self):
        original = self.file(self.root/'originals/front/20260728-000000.mp4')
        legacy = self.file(self.root/'originals/front_main/2026-07-28_00-00-00-123456.mp4')
        cache = self.file(self.p.RECORDING_CACHE_DIR/'front_0/2026-07-28/timeline.mp4')
        foreign = self.file(cache.parent/'family.mp4')
        thumbnail = self.file(self.p.RECORDING_THUMB_DIR/'front_0/20260728-000000.mp4.jpg')
        orphan = self.file(self.p.RECORDING_THUMB_DIR/'front_0/scrub/20260727-000000.mp4.100-123.5.jpg')
        unknown = self.file(self.p.RECORDING_THUMB_DIR/'front_0/family.jpg')
        abandoned = self.file(self.p.RECORDING_THUMB_DIR/'front_0/.20260728-000000.mp4.jpg.123456.tmp')
        before = original.read_bytes()
        report = self.p.cleanup_disposable_storage(self.config)
        self.assertFalse(cache.exists())
        self.assertFalse(orphan.exists())
        self.assertFalse(abandoned.exists())
        self.assertEqual(report['playback']['freed_bytes'],4096)
        for path in (legacy,foreign,thumbnail,unknown):
            self.assertTrue(path.exists(),str(path))
        self.assertEqual(original.read_bytes(),before)

    def test_thumbnail_budget_is_global_across_cameras_and_scrub(self):
        root = self.root/'thumbs'
        old = self.file(root/'front_0/20260728-000000.mp4.jpg',700000,age=10)
        new = self.file(root/'side_1/scrub/20260728-000010.mp4.100-123.5.jpg',700000)
        report = self.s.prune_cache(root,'thumbnail',self.s.MIB)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertLessEqual(report['bytes'],self.s.MIB)

    def test_thumbnail_target_locks_are_shared_and_bounded(self):
        p = self.p
        p.RECORDING_THUMBNAIL_TARGET_LOCKS.clear()
        first = p.recording_thumbnail_target_lock(self.root/'a.jpg')
        self.assertIs(p.recording_thumbnail_target_lock(self.root/'a.jpg'), first, 'one decode per target needs one lock')
        self.assertIsNot(p.recording_thumbnail_target_lock(self.root/'b.jpg'), first)
        first.acquire()
        try:
            for index in range(2000):
                p.recording_thumbnail_target_lock(self.root/f'{index}.jpg')
            self.assertLessEqual(len(p.RECORDING_THUMBNAIL_TARGET_LOCKS), 257)
            self.assertIs(p.recording_thumbnail_target_lock(self.root/'a.jpg'), first, 'a lock in use must survive pruning')
        finally:
            first.release()
        p.RECORDING_THUMBNAIL_TARGET_LOCKS.clear()

    def test_thumbnail_ttl_and_count_limit(self):
        root = self.root/'thumbs'
        expired = self.file(root/'front_0/20260727-000000.mp4.jpg',age=172801)
        older = self.file(root/'front_0/20260728-000000.mp4.jpg',age=10)
        newest = self.file(root/'front_0/20260728-000010.mp4.jpg')
        self.s.prune_cache(root,'thumbnail',self.s.MIB,max_files=1)
        self.assertFalse(expired.exists())
        self.assertFalse(older.exists())
        self.assertTrue(newest.exists())

    def test_pruning_never_follows_links_or_removes_foreign_files(self):
        root = self.root/'thumbs'
        root.mkdir()
        foreign_empty = root/'family-album'
        foreign_empty.mkdir()
        outside = self.file(self.root/'outside/20260728-000000.mp4.jpg')
        (root/'linked-camera').symlink_to(outside.parent,target_is_directory=True)
        (root/outside.name).symlink_to(outside)
        self.s.prune_cache(root,'thumbnail',0)
        self.assertTrue(outside.exists())
        self.assertTrue(foreign_empty.is_dir())
        self.assertTrue((root/outside.name).is_symlink())
        self.assertEqual(self.s.prune_cache(root/'linked-camera','thumbnail',0)['removed'],0)

    def test_day_cache_evicts_whole_bundle_and_retains_recently_accessed_day(self):
        root = self.root/'cache'
        old = self.file(root/'front_0/2026-07-27/timeline.mp4',700000,age=200000)
        old_index = self.file(old.with_suffix('.json'),100,age=200000)
        new = self.file(root/'front_0/2026-07-28/timeline.mp4',700000)
        new_index = self.file(new.with_suffix('.json'),100,age=200000)
        report = self.s.prune_cache(root,'playback',self.s.MIB)
        self.assertFalse(old.exists())
        self.assertFalse(old_index.exists())
        self.assertTrue(new.exists())
        self.assertTrue(new_index.exists())
        self.assertLessEqual(report['bytes'],self.s.MIB)

    def test_in_use_day_cache_is_preserved_under_pressure(self):
        root = self.root/'cache'
        video = self.file(root/'front_0/2026-07-28/timeline.mp4')
        index = self.file(video.with_suffix('.json'))
        self.s.prune_cache(root,'playback',1,protected={video})
        self.assertTrue(video.exists())
        self.assertTrue(index.exists())

    def test_larger_than_budget_day_does_not_build(self):
        config = {**self.config,'nvr':{**self.config['nvr'],'playback_cache_mb':1}}
        self.p.commit_panel_config(config)
        original = self.file(self.root/'originals/front/20260728-000000.mp4',1000000)
        with patch.object(self.p.threading,'Thread') as worker:
            self.assertFalse(self.p.schedule_recording_cache_refresh('front_0',[{'path':original}],10))
            worker.assert_not_called()
        self.assertTrue(original.exists())

    def test_storage_inventory_includes_legacy_and_removed_camera_originals(self):
        self.file(self.root/'originals/removed/20260728-000000.mp4',1234)
        self.file(self.root/'originals/front_main/2026-07-28_00-00-00-123456.mp4',4321)
        self.file(self.root/'originals/family.mp4',9999)
        data = self.p.storage_usage_payload(self.config)
        self.assertEqual(data['recordings_bytes'],5555)
        self.assertEqual(data['playback_cache_limit_bytes'],0)
        self.assertEqual(data['thumbnail_cache_limit_bytes'],32*self.s.MIB)
        self.assertGreater(data['recordings_volume']['total_bytes'],0)

    def test_log_trimming_keeps_the_inode_used_by_running_recorder(self):
        path = self.file(self.root/'ffmpeg.log',10000)
        inode = path.stat().st_ino
        with path.open('ab') as recorder:
            self.s.trim_log(path,limit=1024,tail=100)
            recorder.write(b'next frame\n')
            recorder.flush()
        self.assertEqual(path.stat().st_ino,inode)
        self.assertTrue(path.read_bytes().endswith(b'next frame\n'))
        self.assertLess(path.stat().st_size,1024)

    def test_cache_budgets_survive_config_save_and_reload(self):
        saved = self.p.persist_config_payload({**self.config,'nvr':{**self.config['nvr'],'thumbnail_cache_mb':16,'playback_cache_mb':0}})[-1]
        self.assertEqual(saved['nvr']['thumbnail_cache_mb'],16)
        self.assertEqual(self.p.load_config()['nvr']['playback_cache_mb'],0)

    def test_native_package_does_not_install_a_second_recording_engine(self):
        docker = (Path(__file__).resolve().parents[1] / 'edge-of-infinity/Dockerfile').read_text()
        self.assertNotIn('janus-gateway', docker)
        self.assertNotIn('bluenviron/mediamtx', docker)
        self.assertIn('ffmpeg', docker)
        self.assertIn('COPY --from=edge_rtc', docker)
