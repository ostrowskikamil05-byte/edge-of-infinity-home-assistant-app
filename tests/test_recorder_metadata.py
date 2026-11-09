import json
import unittest
import threading
from unittest.mock import Mock,patch

from test_edge_panel_config import camera,load_panel_module


class RecorderMetadataTests(unittest.TestCase):
    def test_missing_desired_recorder_and_rtsp_rejection_are_visible_in_diagnostics(self):
        p=load_panel_module();config=p.normalize_config({'cameras':[camera('unv','192.0.2.10','main'),camera('hik','192.0.2.11','main')]})
        failed={'code':'sdp_invalid','phase':'describe','rtsp_method':'DESCRIBE','rtsp_status':200}
        runtime={'edge_rtc':{'sources':[{'camera_id':'unv','ready':False,'upstream_connections':0,'packets':0,'connection':{'last_error':failed}}]}}
        rows=p.build_panel_diagnostics(config,{'active_recorders':[{'key':'hik_1'}]}, {},[],runtime=runtime)
        recorders=next(row for row in rows if row['title']=='NVR recorders')
        self.assertEqual(recorders['severity'],'warning');self.assertEqual(recorders['payload']['missing_recorders'],['unv_0'])
        connection=next(row for row in rows if row['title'].startswith('RTSP'))
        self.assertEqual(connection['severity'],'error');self.assertIn('sdp_invalid',connection['detail']);self.assertIn('DESCRIBE',connection['detail'])
        with patch.object(p,'engine_runtime_status',return_value=runtime) as snapshot:
            exported=p.collect_panel_logs();snapshot.assert_called_once()
        self.assertIs(exported['runtime_config'],runtime)
        self.assertTrue(any(row['title'].startswith('RTSP') and 'sdp_invalid' in row['detail'] for row in exported['diagnostics']))

    def test_live_status_uses_shared_registry_and_not_a_timing_sensitive_rtsp_probe(self):
        p=load_panel_module();p.EDGE_RTC_ENABLED=True
        config=p.normalize_config({'cameras':[camera('unv','192.0.2.15','main')]});p.commit_panel_config(config)
        source={'camera_id':'unv','paths':['unv_main','unv_sub'],'ready':True,'upstream_connections':1,'packets':600,'video':{'codec_name':'h265','parameters_ready':True,'width':3840,'height':2160,'fps':20},'audio':{'codec':'mp4a.40.2','sampleRate':48000,'channels':1}}
        with patch.object(p,'edge_rtc_status',return_value={'api_reachable':True,'sources':[source]}),patch.object(p,'probe_rtsp_stream',side_effect=AssertionError('extra RTSP probe')):
            row=p.refresh_status()['cameras'][0]
        self.assertEqual(row['status'],'online');self.assertEqual(row['status_source'],'edge_rtc_registry');self.assertEqual(row['live_video_codec'],'hevc');self.assertEqual(row['audio_codec'],'aac');self.assertEqual(row['width'],'3840')
        source['ready']=False
        with patch.object(p,'edge_rtc_status',return_value={'api_reachable':True,'sources':[source]}),patch.object(p,'probe_rtsp_stream',side_effect=AssertionError('extra RTSP probe')):
            row=p.refresh_status()['cameras'][0]
        self.assertEqual(row['status'],'checking');self.assertNotIn('credentials',row['detail'])

    def test_aac_is_identified_from_existing_registry_without_probe_and_copied(self):
        p=load_panel_module();cam=camera('unv','192.0.2.15','main')
        source={'camera_id':'unv','paths':['unv_main','unv_sub'],'ready':True,'video':{'codec_name':'h265','codec':'hev1.1.6.L120.90'},'audio':{'codec':'mp4a.40.2','sampleRate':48000,'channels':1}}
        with patch.object(p,'edge_rtc_status',return_value={'api_reachable':True,'sources':[source]}),patch.object(p,'probe_rtsp_stream',side_effect=AssertionError('extra probe')):
            metadata=p.recording_registry_metadata(cam,0,'main',wait_seconds=0)
            mode,video,audio,_=p.recording_codec_mode('rtsp://shared/unv_main',{},metadata)
        command=p.build_recording_command('rtsp://shared/unv_main','record.mp4',10,mode,video,audio,48000,1)
        self.assertEqual(video,'hevc');self.assertEqual(audio,'aac');self.assertEqual(command[command.index('-c:a')+1],'copy');self.assertEqual(command[command.index('-c:v')+1],'copy')
        self.assertNotIn('-af',command);self.assertEqual(command[command.index('-tag:v')+1],'hvc1')
        self.assertGreaterEqual(int(command[command.index('-probesize')+1]),1024*1024)

    def test_aliases_match_and_unready_source_is_not_guessed(self):
        p=load_panel_module();cam=camera('alias','192.0.2.15','main')
        source={'camera_id':'original','paths':['alias_main'],'ready':True,'video':{'codec_name':'h264'},'audio':{'codec':'pcma','sampleRate':8000,'channels':1}}
        with patch.object(p,'edge_rtc_status',return_value={'sources':[source]}):
            self.assertEqual(p.recording_registry_metadata(cam,0,'main',wait_seconds=0)['audio']['codec_name'],'pcm_alaw')
        with patch.object(p,'edge_rtc_status',return_value={'api_reachable':True,'sources':[]}),patch.object(p,'probe_rtsp_stream') as probe:
            with self.assertRaisesRegex(RuntimeError,'metadata_pending'):p.recording_registry_metadata(cam,0,'main',wait_seconds=0)
            probe.assert_not_called()

    def test_pending_metadata_has_safe_phase_and_does_not_reuse_old_ffmpeg_errors(self):
        p=load_panel_module();cam=camera('unv','192.0.2.15','main')
        config=p.normalize_config({'cameras':[{**cam,'record':True}]})
        with patch.object(p,'start_recording',side_effect=p.RecordingSourcePending({'phase':'parameter_sets_pending','source_ready':True,'parameters_ready':False,'onvif_required':False})),patch.object(p,'start_recording_source_retry') as retry,patch.object(p,'recording_log_tail',side_effect=AssertionError('stale log attached')),patch.object(p,'write_debug_event') as log:
            rows=p.ensure_configured_recordings(config,'boot')
            self.assertEqual(rows[0]['action'],'waiting_source');self.assertNotIn('log_tail',rows[0]);retry.assert_called_once()
            self.assertNotIn('recording_autostart_failed',[call.args[0] for call in log.call_args_list])
            status=p.recording_status_response(config,{'summary':['1']})['cameras'][0]
            self.assertEqual(status['recording_status'],'waiting_source');self.assertFalse(status['source_wait']['onvif_required'])
            key=p.recording_key(config['cameras'][0],0)
            p.RECORDING_SOURCE_WAIT[key]['_since']=p.time.monotonic()-31
            self.assertEqual(p.ensure_configured_recordings(config,'source_retry')[0]['severity'],'warning')

    def test_real_recorder_failure_remains_an_error_and_is_not_retried_as_metadata_waiting(self):
        p=load_panel_module();config=p.normalize_config({'cameras':[camera('front','192.0.2.15','main')]})
        key=p.recording_key(config['cameras'][0],0);p.RECORDING_SOURCE_WAIT[key]={'phase':'parameter_sets_pending'}
        with patch.object(p,'start_recording',side_effect=RuntimeError('ffmpeg_exited_immediately')),patch.object(p,'recording_log_tail',return_value='dimensions not set'),patch.object(p,'write_debug_event') as log,patch.object(p,'start_recording_source_retry') as retry:
            result=p.ensure_configured_recordings(config,'source_retry')[0]
            self.assertEqual(result['action'],'start_failed');self.assertEqual(result['log_tail'],'dimensions not set')
            self.assertIn('recording_autostart_failed',[call.args[0] for call in log.call_args_list]);retry.assert_not_called()
        self.assertFalse(p.RECORDING_SOURCE_WAIT)

    def test_background_retry_starts_original_aac_recording_without_ui_onvif_or_probe(self):
        p=load_panel_module();p.EDGE_RTC_ENABLED=True;p.RECORDING_SOURCE_RETRY_SECONDS=.02
        config=p.normalize_config({'cameras':[{**camera('unv','192.0.2.15','main'),'record':True}],
                                   'storage':{'recordings_dir':str(p.HOME_DIR/'recordings')}})
        p.commit_panel_config(config)
        source={'camera_id':'unv','paths':['unv_main'],'ready':True,'video':{'codec_name':'h264','parameters_ready':False},
                'audio':{'codec':'mp4a.40.2','sampleRate':48000,'channels':1}}
        metadata=p.recording_registry_metadata;started=threading.Event();events=[]
        def log(event,data):
            events.append(event)
            if event=='recording_start':started.set()
        process=Mock(pid=123);process.poll.return_value=None
        with patch.object(p,'edge_rtc_status',return_value={'api_reachable':True,'sources':[source]}),patch.object(p,'recording_registry_metadata',side_effect=lambda camera,index,stream_name,wait_seconds=4:metadata(camera,index,stream_name,wait_seconds=0)),patch.object(p,'probe_rtsp_stream',side_effect=AssertionError('extra stream')),patch.object(p,'onvif_request',side_effect=AssertionError('ONVIF dependency')),patch.object(p,'write_debug_event',side_effect=log),patch.object(p.subprocess,'Popen',return_value=process) as spawn,patch.object(p,'cleanup_old_recordings',return_value=0) as cleanup,patch.object(p.shutil,'which',return_value='/usr/bin/ffmpeg'):
            try:
                result=p.ensure_configured_recordings(config,'boot')
                self.assertEqual(result[0]['action'],'waiting_source');spawn.assert_not_called();cleanup.assert_not_called()
                source['video']={'codec_name':'h264','parameters_ready':True,'width':3840,'height':2160}
                self.assertTrue(started.wait(4),'source readiness did not resume recording independently of the panel')
                self.assertFalse(p.RECORDING_SOURCE_WAIT);self.assertEqual(spawn.call_count,1)
                command=spawn.call_args.args[0]
                self.assertEqual(command[command.index('-c:v')+1],'copy');self.assertEqual(command[command.index('-c:a')+1],'copy')
                self.assertGreaterEqual(int(command[command.index('-probesize')+1]),1024*1024)
                self.assertGreaterEqual(int(command[command.index('-analyzeduration')+1]),2000000)
                self.assertNotIn('recording_autostart_failed',events)
            finally:p.RECORDING_SOURCE_RETRY_STOP.set()

    def test_retry_uses_current_config_and_keeps_original_indices(self):
        p=load_panel_module();config=p.normalize_config({'cameras':[{**camera('first','192.0.2.11','main'),'record':True},{**camera('second','192.0.2.12','main'),'record':True}]})
        key=p.recording_key(config['cameras'][1],1);p.RECORDING_SOURCE_WAIT[key]={'phase':'video_metadata_missing'}
        with patch.object(p,'load_config',return_value=config),patch.object(p,'start_recording',return_value={'started':True}) as start:
            p.retry_pending_recordings_once()
            start.assert_called_once_with(config['cameras'][1],1,metadata_wait_seconds=0)
        self.assertFalse(p.RECORDING_SOURCE_WAIT)
        p.RECORDING_SOURCE_WAIT[key]={'phase':'video_metadata_missing'};config['cameras'][1]['record']=False
        with patch.object(p,'load_config',return_value=config),patch.object(p,'start_recording') as start:
            p.retry_pending_recordings_once();start.assert_not_called()
        self.assertFalse(p.RECORDING_SOURCE_WAIT)
        config['cameras'][1]['record']=True;p.RECORDING_SOURCE_WAIT[key]={'phase':'video_metadata_missing'}
        p.stop_recording(config['cameras'][1],1)
        with patch.object(p,'load_config',return_value=config),patch.object(p,'start_recording') as start:
            p.retry_pending_recordings_once();start.assert_not_called()

    def test_ffmpeg_command_is_not_a_runtime_error_but_actual_failure_is(self):
        p=load_panel_module();header='=== Edge recording start ===\n'+json.dumps({'event':'recording_start_command','command':['ffmpeg','-loglevel','error','-i','rtsp://shared/failed-name']},indent=2)+'\n'
        self.assertEqual(p.diagnostic_from_tail('Recording FFmpeg ffmpeg.log',header)['severity'],'ok')
        failure=header+'[rtsp @ 0x123] method DESCRIBE failed: 503 Service Unavailable\n'
        self.assertEqual(p.diagnostic_from_tail('Recording FFmpeg ffmpeg.log',failure)['severity'],'error')
        warning=header+'[aac @ 0x123] Non-monotonic DTS; changing to 42\n'
        self.assertEqual(p.diagnostic_from_tail('Recording FFmpeg ffmpeg.log',warning)['severity'],'warning')
        self.assertEqual(p.ffmpeg_runtime_messages(header[:-12]),'')

    def test_archive_keys_map_to_camera_directories_not_api_keys(self):
        from test_edge_rtc import builder
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            payload=builder().build_config({'cameras':[camera('hikvision_1','192.0.2.15','main')],'storage':{'recordings_dir':root}},root)
        self.assertEqual(payload['archives'],{'hikvision_1_0':'hikvision_1'})


if __name__=='__main__':unittest.main()
