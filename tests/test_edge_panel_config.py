import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "edge-of-infinity" / "rootfs" / "usr" / "share" / "edge-of-infinity" / "edge-panel.py"


def load_panel_module():
    temp_root = Path(tempfile.mkdtemp())
    os.environ["EDGE_HOME_DIR"] = str(temp_root / "home")
    os.environ["EDGE_DATA_DIR"] = str(temp_root / "data")
    os.environ["EDGE_HOME_CONFIG"] = str(temp_root / "home" / "edge.json")
    os.environ["EDGE_ADDON_CONFIG"] = str(temp_root / "addon-config" / "edge.json")
    os.environ["EDGE_MEDIAMTX_CONFIG"] = str(temp_root / "runtime" / "mediamtx.yml")
    os.environ["EDGE_JANUS_CONFIG_DIR"] = str(temp_root / "runtime" / "janus")
    spec = importlib.util.spec_from_file_location(f"edge_panel_test_{temp_root.name}", PANEL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def camera(camera_id, host, snapshot_stream):
    return {
        "id": camera_id,
        "name": camera_id.replace("_", " ").title(),
        "vendor": "hikvision",
        "host": host,
        "username": "admin",
        "password": "secret",
        "rtsp_main": f"rtsp://admin:secret@{host}:554/Streaming/Channels/101",
        "rtsp_sub": f"rtsp://admin:secret@{host}:554/Streaming/Channels/102",
        "enabled": True,
        "record": True,
        "low_latency": True,
        "snapshot_stream": snapshot_stream,
        "live_stream": "main",
        "tile_stream": "sub",
        "record_stream": "main",
    }


class EdgePanelConfigTests(unittest.TestCase):
    def test_panel_save_uses_dom_snapshot_and_prevents_plain_form_submit(self):
        html = PANEL_PATH.read_text(encoding="utf-8")

        self.assertIn("function cameraFormSnapshot()", html)
        self.assertIn("function edgeFormSnapshot()", html)
        self.assertIn("collectConfig({ refreshGenerated: true })", html)
        self.assertIn("form.addEventListener('submit'", html)
        self.assertIn("event.preventDefault()", html)
        self.assertIn("host=${host}", html)
        self.assertIn("ui_save_edge_settings_click", html)
        self.assertNotIn("keepalive: true", html)

    def test_panel_exposes_active_version_for_runtime_diagnostics(self):
        panel = load_panel_module()

        self.assertEqual(panel.APP_VERSION, "0.10.26")
        self.assertEqual(panel.EdgeHandler.server_version, "EdgePanel/0.10.26")
        self.assertEqual(panel.health_payload()["server_version"], "EdgePanel/0.10.26")
        self.assertEqual(panel.collect_panel_logs()["server_version"], "EdgePanel/0.10.26")
        self.assertIn("v0.10.26", panel.INDEX_HTML)
        self.assertIn(panel.UI_BUILD, panel.INDEX_HTML)

    def test_chunked_json_request_body_is_read_for_ingress_saves(self):
        panel = load_panel_module()
        raw = b'{"cameras":[{"id":"hikvision_1","host":"192.168.33.136"}]}'
        chunked = b"".join([
            b"10\r\n", raw[:16], b"\r\n",
            f"{len(raw[16:]):x}\r\n".encode("ascii"), raw[16:], b"\r\n",
            b"0\r\n\r\n",
        ])
        handler = object.__new__(panel.EdgeHandler)
        handler.headers = {"Transfer-Encoding": "chunked", "Content-Type": "application/json"}
        handler.rfile = io.BytesIO(chunked)

        payload = panel.EdgeHandler.read_body_json(handler)

        self.assertEqual(payload["cameras"][0]["host"], "192.168.33.136")
        self.assertEqual(handler._last_body_info["bytes_read"], len(raw))

    def test_config_save_refuses_empty_request_body_before_reusing_existing_config(self):
        source = PANEL_PATH.read_text(encoding="utf-8")

        self.assertIn("config_save_empty_body", source)
        self.assertIn("empty_request_body", source)

    def test_supervisor_config_uses_current_app_config_map_type(self):
        config_text = (ROOT / "edge-of-infinity" / "config.yaml").read_text(encoding="utf-8")

        self.assertIn("type: app_config", config_text)
        self.assertNotIn("type: addon_config", config_text)

    def test_save_pipeline_preserves_submitted_stream_roles(self):
        panel = load_panel_module()
        existing = {
            "server": {},
            "storage": {},
            "cameras": [
                camera("hikvision_1", "192.168.33.21", "main"),
                camera("hikvision_2", "192.168.33.135", "main"),
            ],
        }
        panel.write_json(panel.CONFIG_PATH, existing)

        raw_payload = json.loads(json.dumps(existing))
        raw_payload["cameras"][0]["snapshot_stream"] = "sub"
        raw_payload["cameras"][0]["live_stream"] = "sub"
        raw_payload["cameras"][0]["tile_stream"] = "sub"
        raw_payload["cameras"][0]["record_stream"] = "sub"
        raw_payload["cameras"][1]["snapshot_stream"] = "main"

        merged_payload = panel.merge_existing_camera_values(raw_payload)
        normalized_payload = panel.normalize_config(merged_payload)
        saved_payload = panel.preserve_submitted_stream_choices(
            json.loads(json.dumps(normalized_payload)),
            merged_payload,
            raw_payload,
        )
        panel.write_json(panel.CONFIG_PATH, saved_payload)
        panel.save_panel_camera_overrides(saved_payload)
        panel.save_stream_overrides(saved_payload)
        loaded_payload = panel.preserve_submitted_stream_choices(panel.load_config(), merged_payload, raw_payload)

        self.assertEqual(loaded_payload["cameras"][0]["snapshot_stream"], "sub")
        self.assertEqual(loaded_payload["cameras"][0]["live_stream"], "sub")
        self.assertEqual(loaded_payload["cameras"][0]["tile_stream"], "sub")
        self.assertEqual(loaded_payload["cameras"][0]["record_stream"], "sub")
        self.assertEqual(loaded_payload["cameras"][1]["snapshot_stream"], "main")

    def test_runtime_config_wins_over_stale_override_files(self):
        panel = load_panel_module()
        existing = {
            "server": {},
            "storage": {},
            "cameras": [
                camera("hikvision_1", "192.168.33.21", "sub"),
                camera("hikvision_2", "192.168.33.135", "main"),
            ],
        }
        panel.write_json(panel.CONFIG_PATH, existing)
        panel.save_panel_camera_overrides(existing)
        panel.save_stream_overrides(existing)

        rewritten = json.loads(json.dumps(existing))
        rewritten["cameras"][0]["host"] = "192.168.1.64"
        rewritten["cameras"][0]["username"] = "old-admin"
        rewritten["cameras"][0]["password"] = "old-secret"
        rewritten["cameras"][0]["rtsp_main"] = "rtsp://old-admin:old-secret@192.168.1.64:554/Streaming/Channels/101"
        rewritten["cameras"][0]["rtsp_sub"] = "rtsp://old-admin:old-secret@192.168.1.64:554/Streaming/Channels/102"
        rewritten["cameras"][0]["onvif_url"] = "http://192.168.1.64:80/onvif/device_service"
        rewritten["cameras"][0]["isapi_base_url"] = "http://192.168.1.64"
        rewritten["cameras"][0]["enabled"] = False
        rewritten["cameras"][0]["record"] = False
        rewritten["cameras"][0]["low_latency"] = False
        rewritten["cameras"][0]["snapshot_stream"] = "main"
        rewritten["cameras"][0]["live_stream"] = "sub"
        rewritten["cameras"][0]["tile_stream"] = "main"
        rewritten["cameras"][0]["record_stream"] = "sub"
        panel.write_json(panel.CONFIG_PATH, rewritten)

        loaded = panel.load_config()

        self.assertEqual(loaded["cameras"][0]["host"], "192.168.1.64")
        self.assertEqual(loaded["cameras"][0]["username"], "old-admin")
        self.assertEqual(loaded["cameras"][0]["password"], "old-secret")
        self.assertEqual(loaded["cameras"][0]["rtsp_main"], "rtsp://old-admin:old-secret@192.168.1.64:554/Streaming/Channels/101")
        self.assertEqual(loaded["cameras"][0]["rtsp_sub"], "rtsp://old-admin:old-secret@192.168.1.64:554/Streaming/Channels/102")
        self.assertEqual(loaded["cameras"][0]["onvif_url"], "http://192.168.1.64:80/onvif/device_service")
        self.assertEqual(loaded["cameras"][0]["isapi_base_url"], "http://192.168.1.64")
        self.assertFalse(loaded["cameras"][0]["enabled"])
        self.assertFalse(loaded["cameras"][0]["record"])
        self.assertFalse(loaded["cameras"][0]["low_latency"])
        self.assertEqual(loaded["cameras"][0]["snapshot_stream"], "main")
        self.assertEqual(loaded["cameras"][0]["live_stream"], "sub")
        self.assertEqual(loaded["cameras"][0]["tile_stream"], "main")
        self.assertEqual(loaded["cameras"][0]["record_stream"], "sub")

    def test_runtime_edge_json_wins_over_stale_panel_config_and_override_files(self):
        panel = load_panel_module()
        runtime = {
            "server": {},
            "storage": {},
            "cameras": [
                camera("hikvision_1", "192.168.33.21", "main"),
                camera("hikvision_2", "192.168.33.135", "main"),
            ],
        }
        panel.write_json(panel.CONFIG_PATH, runtime)
        panel.save_panel_camera_overrides(runtime)
        panel.save_stream_overrides(runtime)

        stale_panel = json.loads(json.dumps(runtime))
        stale_panel["cameras"][0]["host"] = "192.168.1.64"
        stale_panel["cameras"][0]["snapshot_stream"] = "sub"
        stale_panel["cameras"][0]["live_stream"] = "sub"
        stale_panel["cameras"][0]["tile_stream"] = "sub"
        stale_panel["cameras"][0]["record_stream"] = "sub"
        panel.write_json(panel.PANEL_CONFIG_PATH, stale_panel)
        os.utime(panel.PANEL_CONFIG_PATH, (2000, 2000))
        os.utime(panel.CONFIG_PATH, (1000, 1000))

        loaded = panel.load_config()
        mirrored = panel.read_json(panel.PANEL_CONFIG_PATH, {})

        self.assertEqual(loaded["cameras"][0]["host"], "192.168.33.21")
        self.assertEqual(loaded["cameras"][0]["snapshot_stream"], "main")
        self.assertEqual(loaded["cameras"][0]["live_stream"], "main")
        self.assertEqual(loaded["cameras"][0]["tile_stream"], "sub")
        self.assertEqual(loaded["cameras"][0]["record_stream"], "main")
        self.assertEqual(mirrored["cameras"][0]["host"], "192.168.33.21")

    def test_runtime_edge_json_is_adopted_over_panel_config_regardless_of_mtime(self):
        panel = load_panel_module()
        panel_payload = {
            "server": {},
            "storage": {},
            "cameras": [camera("hikvision_1", "192.168.33.21", "main")],
        }
        runtime_payload = {
            "server": {},
            "storage": {},
            "cameras": [camera("hikvision_1", "192.168.33.136", "sub")],
        }
        panel.write_json(panel.PANEL_CONFIG_PATH, panel_payload)
        panel.write_json(panel.CONFIG_PATH, runtime_payload)
        os.utime(panel.PANEL_CONFIG_PATH, (2000, 2000))
        os.utime(panel.CONFIG_PATH, (1000, 1000))

        loaded = panel.load_config()
        mirrored = panel.read_json(panel.PANEL_CONFIG_PATH, {})

        self.assertEqual(loaded["cameras"][0]["host"], "192.168.33.136")
        self.assertEqual(mirrored["cameras"][0]["host"], "192.168.33.136")

    def test_legacy_panel_config_is_migrated_when_edge_json_is_missing(self):
        panel = load_panel_module()
        panel_payload = {
            "server": {},
            "storage": {},
            "cameras": [camera("hikvision_1", "192.168.33.136", "sub")],
        }
        panel.write_json(panel.PANEL_CONFIG_PATH, panel_payload)

        loaded = panel.load_config()
        runtime = panel.read_json(panel.CONFIG_PATH, {})

        self.assertEqual(loaded["cameras"][0]["host"], "192.168.33.136")
        self.assertEqual(runtime["cameras"][0]["host"], "192.168.33.136")

    def test_prepare_save_keeps_ui_changes_without_bouncing_to_existing_config(self):
        panel = load_panel_module()
        existing = {
            "server": {},
            "storage": {},
            "cameras": [camera("hikvision_1", "192.168.33.21", "main")],
        }
        panel.write_json(panel.CONFIG_PATH, existing)

        raw_payload = json.loads(json.dumps(existing))
        raw_payload["cameras"][0]["camera_number"] = "1"
        raw_payload["cameras"][0]["rtsp_main_channel"] = "201"
        raw_payload["cameras"][0]["rtsp_sub_channel"] = "202"
        raw_payload["cameras"][0]["tile_stream"] = "sub"
        raw_payload["cameras"][0]["live_stream"] = "sub"
        raw_payload["cameras"][0]["record_stream"] = "sub"
        raw_payload["cameras"][0]["snapshot_stream"] = "sub"

        _, _, _, final_payload = panel.prepare_config_for_save(raw_payload)
        committed = panel.commit_panel_config(final_payload)
        loaded = panel.load_config()

        self.assertEqual(committed["cameras"][0]["rtsp_main_channel"], "201")
        self.assertEqual(committed["cameras"][0]["rtsp_sub_channel"], "202")
        self.assertEqual(committed["cameras"][0]["live_stream"], "sub")
        self.assertEqual(committed["cameras"][0]["record_stream"], "sub")
        self.assertEqual(loaded["cameras"][0]["rtsp_main_channel"], "201")
        self.assertEqual(loaded["cameras"][0]["rtsp_sub_channel"], "202")
        self.assertEqual(loaded["cameras"][0]["live_stream"], "sub")
        self.assertEqual(loaded["cameras"][0]["record_stream"], "sub")

    def test_commit_panel_config_clears_legacy_override_files(self):
        panel = load_panel_module()
        payload = {
            "server": {},
            "storage": {},
            "cameras": [camera("hikvision_1", "192.168.33.21", "sub")],
        }
        panel.save_panel_camera_overrides(payload)
        panel.save_stream_overrides(payload)
        self.assertTrue(panel.PANEL_CAMERA_OVERRIDES_PATH.exists())
        self.assertTrue(panel.STREAM_OVERRIDES_PATH.exists())

        committed = panel.commit_panel_config(payload)

        self.assertEqual(committed["cameras"][0]["live_stream"], "main")
        self.assertFalse(panel.PANEL_CAMERA_OVERRIDES_PATH.exists())
        self.assertFalse(panel.STREAM_OVERRIDES_PATH.exists())

    def test_commit_panel_config_mirrors_addon_config_file(self):
        panel = load_panel_module()
        payload = {
            "server": {},
            "storage": {},
            "cameras": [camera("hikvision_1", "192.168.33.136", "sub")],
        }

        committed = panel.commit_panel_config(payload)
        addon_mirror = panel.read_json(panel.ADDON_CONFIG_PATH, {})

        self.assertEqual(addon_mirror, committed)
        self.assertEqual(addon_mirror["cameras"][0]["host"], "192.168.33.136")

    def test_home_live_tiles_honor_tile_stream_and_enable_audio_controls(self):
        html = PANEL_PATH.read_text(encoding="utf-8")

        self.assertIn("function chooseHomePreviewStream(camera)", html)
        self.assertIn("configured_tile_stream", html)
        self.assertNotIn("main_stream_above_tile_width", html)
        self.assertIn("function mediaMtxPlayerUrl(url)", html)
        self.assertIn("controls=true&muted=false&autoplay=true&playsInline=true&disablepictureinpicture=true", html)

    def test_janus_streaming_mounts_do_not_disable_audio(self):
        runner = (ROOT / "edge-of-infinity" / "rootfs" / "usr" / "bin" / "edge-app-run").read_text(encoding="utf-8")

        self.assertIn("audio = true", runner)
        self.assertNotIn("audio = false", runner)

    def test_runtime_engine_config_uses_saved_camera_values(self):
        panel = load_panel_module()
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {"recordings_dir": str(panel.HOME_DIR / "recordings")},
                "live": {
                    "mobile_webrtc_public_url": "http://edge.example.com:8889",
                    "mobile_webrtc_public_hosts": "homeassistant.local",
                    "mobile_webrtc_ice_transport": "tcp",
                },
                "cameras": [
                    {
                        "id": "hikvision_1",
                        "vendor": "hikvision",
                        "host": "192.168.33.136",
                        "username": "admin",
                        "password": "secret",
                        "live_stream": "sub",
                        "tile_stream": "sub",
                        "record_stream": "sub",
                        "enabled": True,
                        "record": True,
                    }
                ],
            }
        )

        result = panel.sync_runtime_engine_config(payload, "test")
        mediamtx_config = panel.MEDIAMTX_CONFIG_PATH.read_text(encoding="utf-8")
        janus_config = (panel.JANUS_CONFIG_DIR / "janus.plugin.streaming.jcfg").read_text(encoding="utf-8")

        self.assertTrue(result["mediamtx"]["written"])
        self.assertIn("edge.example.com", mediamtx_config)
        self.assertIn("hikvision_1_sub:", mediamtx_config)
        self.assertIn("rtsp://admin:secret@192.168.33.136:554/Streaming/Channels/102", mediamtx_config)
        self.assertIn("record: yes", mediamtx_config)
        self.assertIn("audio = true", janus_config)
        self.assertIn("hikvision_1_sub", janus_config)

    def test_panel_has_fullscreen_and_recording_timeline_controls(self):
        html = PANEL_PATH.read_text(encoding="utf-8")

        self.assertIn("function requestEdgeFullscreen", html)
        self.assertIn("data-fullscreen-live", html)
        self.assertIn("data-recording-fullscreen", html)
        self.assertIn("function selectRecordingAtOffset", html)
        self.assertIn("edge-soft-fullscreen", html)
        self.assertIn("soft_fallback", html)

    def test_nvr_timeline_does_not_rerender_during_active_playback(self):
        html = PANEL_PATH.read_text(encoding="utf-8")

        self.assertIn("function isNvrPlaybackBusy", html)
        self.assertIn("ui_recording_status_render_skipped", html)
        self.assertIn("function setCurrentRecordingTime", html)
        self.assertIn("function recordingStreamUrl", html)
        self.assertIn("recordings-stream/", html)
        self.assertIn("function recordingPlaybackModeForClient", html)
        self.assertIn("daily-cache-nvr-v1", html)
        self.assertIn("server_cache_mp4", html)
        self.assertIn("selectedRecordingDay", html)
        self.assertIn("function selectRecordingDay", html)
        self.assertIn("function moveRecordingDay", html)
        self.assertIn("data-recording-day-swipe", html)
        self.assertIn("recording-day-tile", html)
        self.assertIn("function seekRecordingCache", html)
        self.assertIn("ui_recording_cache_seek", html)
        self.assertIn("ui_recording_cache_resume", html)
        self.assertIn("recording-cache/", html)
        self.assertIn("nvr-playback-cache-segments", html)
        self.assertIn("Recording cache", html)
        self.assertIn("server_file_sequence", html)
        self.assertIn("function switchRecordingVideoToFile", html)
        self.assertIn("ui_recording_server_file_switch", html)
        self.assertIn("ui_recording_server_file_fast_seek", html)
        self.assertIn("data-recording-stream-start", html)
        self.assertIn("data-recording-timeline-label", html)
        self.assertIn("recording-native-timeline", html)
        self.assertIn("function formatTimestampSeconds", html)
        self.assertIn("function isMobileNvrPlayback", html)
        self.assertIn("function seekCurrentRecordingStream", html)
        self.assertIn("data-recording-playback-mode", html)
        self.assertIn("continuous_stream", html)
        self.assertIn("ui_recording_continuous_resume", html)
        self.assertIn("function recordingVideoDiagnostics", html)
        self.assertIn("ui_recording_video_error", html)
        self.assertIn("webkit-playsinline", html)
        self.assertIn("controls preload=", html)
        self.assertIn("recording-thumb", html)
        self.assertIn("THUMB_PLACEHOLDER", html)
        self.assertIn("data-recording-thumb-src", html)
        self.assertIn("function scheduleRecordingThumbnailHydration", html)
        self.assertIn("thumbnail_url", html)
        self.assertIn("max-height: 232px", html)
        self.assertNotIn("data-recording-scrub", html)
        self.assertNotIn("nvrGrid.addEventListener('input'", html)
        self.assertIn("nvrGrid.addEventListener('timeupdate'", html)
        self.assertIn("nvrGrid.addEventListener('ended'", html)
        self.assertIn("ui_recording_stream_ended", html)
        self.assertNotIn("captureRecordingSnapshot", html)
        self.assertNotIn("recordingSnapshots", html)
        self.assertNotIn("recording_auto_next_segment", html)

    def test_recording_mp4_route_supports_mobile_video_probe_requests(self):
        html = PANEL_PATH.read_text(encoding="utf-8")

        self.assertIn("def do_HEAD", html)
        self.assertIn("self.serve_recording_cache(path, send_body=False)", html)
        self.assertIn("def serve_recording_cache", html)
        self.assertIn("recording_cache_request", html)
        self.assertIn("self.serve_recording(path, send_body=False)", html)
        self.assertIn("Accept-Ranges", html)
        self.assertIn("Content-Range", html)
        self.assertIn("Content-Disposition", html)
        self.assertIn("recording_file_request", html)
        self.assertIn("Access-Control-Allow-Origin", html)

    def test_live_mobile_settings_are_normalized_and_preserved(self):
        panel = load_panel_module()
        payload = {
            "server": {},
            "storage": {},
            "live": {
                "engine": "janus_webrtc",
                "remote_access_mode": "vps_relay",
                "prebuffer_enabled": True,
                "always_on_enabled": True,
                "always_on_stream_scope": "tile",
                "prebuffer_local_ms": 5000,
                "prebuffer_remote_ms": 2500,
                "mobile_webrtc_public_hosts": "edge.example.com,192.168.33.17",
                "mobile_webrtc_stun_url": "stun:stun.l.google.com:19302",
                "mobile_webrtc_turn_url": "turns:turn.example.com:443",
                "mobile_webrtc_turn_username": "edge",
                "mobile_webrtc_turn_password": "secret",
                "mobile_webrtc_ice_transport": "tcp",
            },
            "cameras": [camera("hikvision_1", "192.168.33.21", "sub")],
        }

        normalized = panel.normalize_config(payload)

        self.assertTrue(normalized["live"]["prebuffer_enabled"])
        self.assertTrue(normalized["live"]["always_on_enabled"])
        self.assertEqual(normalized["live"]["always_on_stream_scope"], "tile")
        self.assertEqual(normalized["live"]["remote_access_mode"], "vps_relay")
        self.assertEqual(normalized["live"]["prebuffer_remote_ms"], 2500)
        self.assertEqual(normalized["live"]["mobile_webrtc_public_hosts"], "edge.example.com,192.168.33.17")
        self.assertEqual(normalized["live"]["mobile_webrtc_turn_url"], "turns:turn.example.com:443")
        self.assertEqual(normalized["live"]["mobile_webrtc_turn_password"], "secret")
        self.assertEqual(normalized["live"]["mobile_webrtc_ice_transport"], "tcp")
        self.assertTrue(normalized["live"]["mobile_webrtc_tcp_only"])

    def test_default_always_on_keeps_tile_stream_warm_without_forcing_4k_main(self):
        panel = load_panel_module()
        test_camera = camera("hikvision_1", "192.168.33.21", "sub")
        test_camera["record"] = False
        test_camera["record_stream"] = "sub"
        test_camera["live_stream"] = "sub"
        test_camera["tile_stream"] = "sub"
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {},
                "live": {"prebuffer_enabled": True, "always_on_enabled": True},
                "cameras": [test_camera],
            }
        )

        result = panel.write_mediamtx_runtime_config(payload)
        text = panel.MEDIAMTX_CONFIG_PATH.read_text(encoding="utf-8")

        self.assertTrue(result["always_on_enabled"])
        self.assertEqual(result["always_on_stream_scope"], "tile")
        self.assertIn("hikvision_1_main:", text)
        self.assertIn("hikvision_1_sub:", text)
        main_block = text.split("  hikvision_1_main:", 1)[1].split("  hikvision_1_sub:", 1)[0]
        sub_block = text.split("  hikvision_1_sub:", 1)[1]
        self.assertIn("sourceOnDemand: yes", main_block)
        self.assertIn("record: no", main_block)
        self.assertIn("sourceOnDemand: no", sub_block)

    def test_all_scope_can_keep_both_mediamtx_paths_started_when_requested(self):
        panel = load_panel_module()
        test_camera = camera("hikvision_1", "192.168.33.21", "sub")
        test_camera["record"] = False
        test_camera["record_stream"] = "sub"
        test_camera["live_stream"] = "sub"
        test_camera["tile_stream"] = "sub"
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {},
                "live": {"prebuffer_enabled": True, "always_on_enabled": True, "always_on_stream_scope": "all"},
                "cameras": [test_camera],
            }
        )

        result = panel.write_mediamtx_runtime_config(payload)
        text = panel.MEDIAMTX_CONFIG_PATH.read_text(encoding="utf-8")

        self.assertEqual(result["always_on_stream_scope"], "all")
        main_block = text.split("  hikvision_1_main:", 1)[1].split("  hikvision_1_sub:", 1)[0]
        sub_block = text.split("  hikvision_1_sub:", 1)[1]
        self.assertIn("sourceOnDemand: no", main_block)
        self.assertIn("sourceOnDemand: no", sub_block)

    def test_selected_stream_warmth_still_works_when_always_on_is_disabled(self):
        panel = load_panel_module()
        test_camera = camera("hikvision_1", "192.168.33.21", "sub")
        test_camera["record"] = False
        test_camera["record_stream"] = "sub"
        test_camera["live_stream"] = "sub"
        test_camera["tile_stream"] = "sub"
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {},
                "live": {"prebuffer_enabled": True, "always_on_enabled": False},
                "cameras": [test_camera],
            }
        )

        result = panel.write_mediamtx_runtime_config(payload)
        text = panel.MEDIAMTX_CONFIG_PATH.read_text(encoding="utf-8")

        self.assertFalse(result["always_on_enabled"])
        main_block = text.split("  hikvision_1_main:", 1)[1].split("  hikvision_1_sub:", 1)[0]
        sub_block = text.split("  hikvision_1_sub:", 1)[1]
        self.assertIn("sourceOnDemand: yes", main_block)
        self.assertIn("sourceOnDemand: no", sub_block)

    def test_edge_settings_exposes_always_on_live_control(self):
        html = PANEL_PATH.read_text(encoding="utf-8")

        self.assertIn("live-always-on-enabled", html)
        self.assertIn("Keep live paths warm after boot", html)
        self.assertIn("live-always-on-stream-scope", html)
        self.assertIn("always_on_stream_scope: get('live-always-on-stream-scope').value", html)
        self.assertIn("always_on_enabled: get('live-always-on-enabled').checked", html)

    def test_legacy_tcp_only_selects_tcp_ice_transport(self):
        panel = load_panel_module()
        normalized = panel.normalize_config(
            {
                "server": {},
                "storage": {},
                "live": {"mobile_webrtc_tcp_only": True},
                "cameras": [camera("hikvision_1", "192.168.33.21", "sub")],
            }
        )

        self.assertEqual(normalized["live"]["mobile_webrtc_ice_transport"], "tcp")
        self.assertTrue(normalized["live"]["mobile_webrtc_tcp_only"])

    def test_camera_number_builds_hikvision_channels_and_rtsp_urls(self):
        panel = load_panel_module()
        payload = {
            "server": {},
            "storage": {},
            "cameras": [
                {
                    "id": "hikvision_2",
                    "vendor": "hikvision",
                    "host": "192.168.33.135",
                    "username": "admin",
                    "password": "secret",
                    "camera_number": "2",
                    "enabled": True,
                    "record": True,
                }
            ],
        }

        normalized = panel.normalize_config(payload)
        camera_config = normalized["cameras"][0]

        self.assertEqual(camera_config["camera_number"], "2")
        self.assertEqual(camera_config["rtsp_main_channel"], "201")
        self.assertEqual(camera_config["rtsp_sub_channel"], "202")
        self.assertEqual(camera_config["rtsp_main"], "rtsp://admin:secret@192.168.33.135:554/Streaming/Channels/201")
        self.assertEqual(camera_config["rtsp_sub"], "rtsp://admin:secret@192.168.33.135:554/Streaming/Channels/202")

    def test_manual_hikvision_channels_are_not_overwritten_by_camera_number(self):
        panel = load_panel_module()
        payload = {
            "server": {},
            "storage": {},
            "cameras": [
                {
                    "id": "hikvision_1",
                    "vendor": "hikvision",
                    "host": "192.168.33.21",
                    "username": "admin",
                    "password": "secret",
                    "camera_number": "1",
                    "rtsp_main_channel": "201",
                    "rtsp_sub_channel": "202",
                    "enabled": True,
                    "record": True,
                }
            ],
        }

        normalized = panel.normalize_config(payload)
        camera_config = normalized["cameras"][0]

        self.assertEqual(camera_config["camera_number"], "1")
        self.assertEqual(camera_config["rtsp_main_channel"], "201")
        self.assertEqual(camera_config["rtsp_sub_channel"], "202")
        self.assertEqual(camera_config["rtsp_main"], "rtsp://admin:secret@192.168.33.21:554/Streaming/Channels/201")
        self.assertEqual(camera_config["rtsp_sub"], "rtsp://admin:secret@192.168.33.21:554/Streaming/Channels/202")

    def test_hikvision_rtsp_rebuilds_when_host_changes_but_old_url_remains_in_form(self):
        panel = load_panel_module()
        payload = {
            "server": {},
            "storage": {},
            "cameras": [
                {
                    "id": "hikvision_1",
                    "vendor": "hikvision",
                    "host": "192.168.33.50",
                    "username": "admin",
                    "password": "new-secret",
                    "camera_number": "1",
                    "rtsp_main_channel": "101",
                    "rtsp_sub_channel": "102",
                    "rtsp_main": "rtsp://admin:old-secret@192.168.33.21:554/Streaming/Channels/101",
                    "rtsp_sub": "rtsp://admin:old-secret@192.168.33.21:554/Streaming/Channels/102",
                    "enabled": True,
                    "record": True,
                }
            ],
        }

        normalized = panel.normalize_config(payload)
        camera_config = normalized["cameras"][0]

        self.assertEqual(camera_config["host"], "192.168.33.50")
        self.assertEqual(camera_config["rtsp_main"], "rtsp://admin:new-secret@192.168.33.50:554/Streaming/Channels/101")
        self.assertEqual(camera_config["rtsp_sub"], "rtsp://admin:new-secret@192.168.33.50:554/Streaming/Channels/102")

    def test_hikvision_onvif_and_isapi_are_rebuilt_when_host_changes(self):
        panel = load_panel_module()
        payload = {
            "server": {},
            "storage": {},
            "cameras": [
                {
                    "id": "hikvision_1",
                    "vendor": "hikvision",
                    "host": "192.168.33.136",
                    "username": "admin",
                    "password": "secret",
                    "rtsp_main": "rtsp://admin:secret@192.168.33.21:554/Streaming/Channels/101",
                    "rtsp_sub": "rtsp://admin:secret@192.168.33.21:554/Streaming/Channels/102",
                    "onvif_url": "rtsp://192.168.33.21:554/Streaming/Channels/101?transportmode=unicast&profile=Profile_1",
                    "isapi_base_url": "http://192.168.33.21/ISAPI/Streaming/channels/101/httpPreview",
                    "enabled": True,
                    "record": True,
                }
            ],
        }

        normalized = panel.normalize_config(payload)
        camera_config = normalized["cameras"][0]

        self.assertEqual(camera_config["rtsp_main"], "rtsp://admin:secret@192.168.33.136:554/Streaming/Channels/101")
        self.assertEqual(camera_config["rtsp_sub"], "rtsp://admin:secret@192.168.33.136:554/Streaming/Channels/102")
        self.assertEqual(camera_config["onvif_url"], "http://192.168.33.136:80/onvif/device_service")
        self.assertEqual(camera_config["isapi_base_url"], "http://192.168.33.136")

    def test_save_then_reload_keeps_changed_connection_and_stream_values(self):
        panel = load_panel_module()
        existing = {
            "server": {},
            "storage": {},
            "cameras": [camera("hikvision_1", "192.168.33.21", "main")],
        }
        panel.write_json(panel.CONFIG_PATH, existing)

        raw_payload = json.loads(json.dumps(existing))
        raw_payload["cameras"][0]["host"] = "192.168.33.50"
        raw_payload["cameras"][0]["password"] = "new-secret"
        raw_payload["cameras"][0]["tile_stream"] = "sub"
        raw_payload["cameras"][0]["live_stream"] = "sub"
        raw_payload["cameras"][0]["record_stream"] = "sub"
        raw_payload["cameras"][0]["snapshot_stream"] = "sub"

        _, _, _, final_payload = panel.prepare_config_for_save(raw_payload)
        panel.commit_panel_config(final_payload)
        loaded = panel.load_config()

        self.assertEqual(loaded["cameras"][0]["host"], "192.168.33.50")
        self.assertEqual(loaded["cameras"][0]["password"], "new-secret")
        self.assertEqual(loaded["cameras"][0]["rtsp_main"], "rtsp://admin:new-secret@192.168.33.50:554/Streaming/Channels/101")
        self.assertEqual(loaded["cameras"][0]["rtsp_sub"], "rtsp://admin:new-secret@192.168.33.50:554/Streaming/Channels/102")
        self.assertEqual(loaded["cameras"][0]["tile_stream"], "sub")
        self.assertEqual(loaded["cameras"][0]["live_stream"], "sub")
        self.assertEqual(loaded["cameras"][0]["record_stream"], "sub")
        self.assertEqual(loaded["cameras"][0]["snapshot_stream"], "sub")

    def test_hikvision_channels_are_saved_and_build_rtsp_urls(self):
        panel = load_panel_module()
        payload = {
            "server": {},
            "storage": {},
            "cameras": [
                {
                    "id": "hikvision_1",
                    "vendor": "hikvision",
                    "host": "192.168.33.21",
                    "username": "admin",
                    "password": "secret",
                    "rtsp_main_channel": "201",
                    "rtsp_sub_channel": "202",
                    "enabled": True,
                    "record": True,
                }
            ],
        }

        normalized = panel.normalize_config(payload)
        camera_config = normalized["cameras"][0]

        self.assertEqual(camera_config["rtsp_main_channel"], "201")
        self.assertEqual(camera_config["rtsp_sub_channel"], "202")
        self.assertEqual(camera_config["rtsp_main"], "rtsp://admin:secret@192.168.33.21:554/Streaming/Channels/201")
        self.assertEqual(camera_config["rtsp_sub"], "rtsp://admin:secret@192.168.33.21:554/Streaming/Channels/202")

    def test_recording_preflight_reports_missing_password(self):
        panel = load_panel_module()
        normalized = panel.normalize_camera(
            {
                "id": "hikvision_1",
                "vendor": "hikvision",
                "host": "192.168.33.21",
                "username": "admin",
                "password": "",
                "record_stream": "main",
                "enabled": True,
                "record": True,
            },
            1,
        )

        self.assertEqual(panel.recording_preflight_error(normalized, "main"), "recording_password_missing")

    def test_recording_status_exposes_preflight_error(self):
        panel = load_panel_module()
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {"recordings_dir": str(panel.HOME_DIR / "recordings")},
                "cameras": [
                    {
                        "id": "hikvision_1",
                        "vendor": "hikvision",
                        "host": "192.168.33.21",
                        "username": "admin",
                        "password": "",
                        "record_stream": "main",
                        "enabled": True,
                        "record": True,
                    }
                ],
            }
        )

        status = panel.recording_status_payload(payload)["cameras"][0]

        self.assertFalse(status["can_record"])
        self.assertEqual(status["record_error"], "recording_password_missing")
        self.assertEqual(status["recording_status"], "blocked")

    def test_recording_source_prefers_mediamtx_rebroadcast_from_panel_camera(self):
        panel = load_panel_module()
        camera_config = panel.normalize_camera(camera("hikvision_1", "192.168.33.21", "sub"), 1)

        stream, source = panel.recording_source_stream(camera_config, 0, "main")

        self.assertEqual(source, "mediamtx_rebroadcast")
        self.assertEqual(stream, "rtsp://127.0.0.1:8556/hikvision_1_main")

    def test_recording_status_marks_enabled_record_camera_as_scheduled(self):
        panel = load_panel_module()
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {"recordings_dir": str(panel.HOME_DIR / "recordings")},
                "cameras": [
                    {
                        "id": "hikvision_1",
                        "vendor": "hikvision",
                        "host": "192.168.33.21",
                        "username": "admin",
                        "password": "secret",
                        "record_stream": "main",
                        "enabled": True,
                        "record": True,
                    }
                ],
            }
        )

        status = panel.recording_status_payload(payload)["cameras"][0]

        self.assertTrue(status["desired_recording"])
        self.assertEqual(status["recording_status"], "scheduled_stopped")

    def test_recording_status_uses_mediamtx_rebroadcast_source(self):
        panel = load_panel_module()
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {"recordings_dir": str(panel.HOME_DIR / "recordings")},
                "cameras": [
                    {
                        "id": "hikvision_1",
                        "vendor": "hikvision",
                        "host": "192.168.33.21",
                        "username": "admin",
                        "password": "secret",
                        "record_stream": "main",
                        "enabled": True,
                        "record": True,
                    }
                ],
            }
        )

        status = panel.recording_status_payload(payload)["cameras"][0]

        self.assertEqual(status["record_source"], "mediamtx_rebroadcast")
        self.assertIn("rtsp://127.0.0.1", status["record_rtsp"])
        self.assertIn("hikvision_1_main", status["record_rtsp"])

    def test_recording_status_exposes_video_timeline_metadata(self):
        panel = load_panel_module()
        panel.MIN_RECORDING_FILE_READY_SECONDS = 0
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {"recordings_dir": str(panel.HOME_DIR / "recordings")},
                "nvr": {"segment_seconds": 12},
                "cameras": [
                    {
                        "id": "hikvision_1",
                        "vendor": "hikvision",
                        "host": "192.168.33.21",
                        "username": "admin",
                        "password": "secret",
                        "record_stream": "main",
                        "enabled": True,
                        "record": True,
                    }
                ],
            }
        )
        panel.write_json(panel.CONFIG_PATH, payload)
        directory = panel.recording_base_dir(payload["cameras"][0], 0)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "20260727-173000.mp4").write_bytes(b"video")
        (directory / "20260727-173012.mp4").write_bytes(b"video")

        status = panel.recording_status_payload(payload)["cameras"][0]
        files = status["files"]

        self.assertEqual(status["segment_seconds"], 12)
        self.assertEqual(status["timeline"]["file_count"], 2)
        self.assertEqual(status["timeline"]["total_seconds"], 24)
        self.assertEqual(files[0]["kind"], "video_segment")
        self.assertEqual(files[0]["duration_seconds"], 12)
        self.assertIn("recording-thumbs/", files[0]["thumbnail_url"])
        self.assertIn("start_ts", files[0])
        self.assertIn("playback_cache", status)
        self.assertIn("source_count", status["playback_cache"])
        self.assertEqual(status["selected_day"], "2026-07-27")
        self.assertEqual(status["days"][0]["day"], "2026-07-27")
        self.assertIn("thumbnail_url", status["days"][0])

    def test_recording_status_can_select_calendar_day(self):
        panel = load_panel_module()
        panel.MIN_RECORDING_FILE_READY_SECONDS = 0
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {"recordings_dir": str(panel.HOME_DIR / "recordings")},
                "nvr": {"segment_seconds": 10},
                "cameras": [
                    {
                        "id": "hikvision_1",
                        "vendor": "hikvision",
                        "host": "192.168.33.21",
                        "username": "admin",
                        "password": "secret",
                        "record_stream": "main",
                        "enabled": True,
                        "record": True,
                    }
                ],
            }
        )
        panel.write_json(panel.CONFIG_PATH, payload)
        directory = panel.recording_base_dir(payload["cameras"][0], 0)
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("20260727-235950.mp4", "20260728-000000.mp4", "20260728-000010.mp4"):
            (directory / name).write_bytes(b"video")

        status = panel.recording_status_payload(payload, {"0": "2026-07-27"})["cameras"][0]

        self.assertEqual(status["selected_day"], "2026-07-27")
        self.assertEqual(status["timeline"]["file_count"], 1)
        self.assertEqual(status["files"][0]["day"], "2026-07-27")
        self.assertEqual(status["playback_cache"]["cache_name"], "2026-07-27")
        self.assertIn("recording-cache/", status["playback_cache"]["url"] or "recording-cache/")

    def test_recording_stream_plan_builds_continuous_concat_from_timeline(self):
        panel = load_panel_module()
        panel.MIN_RECORDING_FILE_READY_SECONDS = 0
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {"recordings_dir": str(panel.HOME_DIR / "recordings")},
                "nvr": {"segment_seconds": 12},
                "cameras": [
                    {
                        "id": "hikvision_1",
                        "vendor": "hikvision",
                        "host": "192.168.33.21",
                        "username": "admin",
                        "password": "secret",
                        "record_stream": "main",
                        "enabled": True,
                        "record": True,
                    }
                ],
            }
        )
        panel.write_json(panel.CONFIG_PATH, payload)
        directory = panel.recording_base_dir(payload["cameras"][0], 0)
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("20260727-173000.mp4", "20260727-173012.mp4", "20260727-173024.mp4"):
            (directory / name).write_bytes(b"video")

        key = panel.recording_key(payload["cameras"][0], 0)
        plan = panel.recording_stream_plan(key, 13)
        concat_text = plan["concat_path"].read_text(encoding="utf-8")

        self.assertEqual(plan["seek_seconds"], 1)
        self.assertEqual(plan["file_count"], 2)
        self.assertEqual(plan["first_file"], "20260727-173012.mp4")
        self.assertIn("20260727-173012.mp4", concat_text)
        self.assertIn("20260727-173024.mp4", concat_text)

    def test_recording_status_hides_unfinalized_recent_mp4_from_mobile_playback(self):
        panel = load_panel_module()
        panel.MIN_RECORDING_FILE_READY_SECONDS = 2.0
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {"recordings_dir": str(panel.HOME_DIR / "recordings")},
                "nvr": {"segment_seconds": 10},
                "cameras": [
                    {
                        "id": "hikvision_1",
                        "vendor": "hikvision",
                        "host": "192.168.33.21",
                        "username": "admin",
                        "password": "secret",
                        "record_stream": "main",
                        "enabled": True,
                        "record": True,
                    }
                ],
            }
        )
        panel.write_json(panel.CONFIG_PATH, payload)
        directory = panel.recording_base_dir(payload["cameras"][0], 0)
        directory.mkdir(parents=True, exist_ok=True)
        old_file = directory / "20260727-173000.mp4"
        pending_file = directory / "20260727-173010.mp4"
        old_file.write_bytes(b"video")
        pending_file.write_bytes(b"video")
        old_ts = time.time() - 10
        os.utime(old_file, (old_ts, old_ts))

        status = panel.recording_status_payload(payload)["cameras"][0]

        self.assertEqual(status["segments_total"], 2)
        self.assertEqual(status["segments"], 1)
        self.assertEqual(status["segments_pending"], 1)
        self.assertEqual([item["name"] for item in status["files"]], ["20260727-173000.mp4"])

    def test_recording_stream_command_outputs_fragmented_mp4(self):
        panel = load_panel_module()
        command = panel.build_recording_stream_command(panel.HOME_DIR / "stream.ffconcat", 4)

        self.assertIn("frag_keyframe+empty_moov+default_base_moof", command)
        self.assertIn("pipe:1", command)
        self.assertIn("-f", command)
        self.assertIn("concat", command)
        self.assertGreater(command.index("-ss"), command.index("-i"))

    def test_recording_cache_status_exposes_server_timeline_mp4(self):
        panel = load_panel_module()
        panel.MIN_RECORDING_FILE_READY_SECONDS = 0
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {"recordings_dir": str(panel.HOME_DIR / "recordings")},
                "nvr": {"segment_seconds": 10},
                "cameras": [
                    {
                        "id": "hikvision_1",
                        "vendor": "hikvision",
                        "host": "192.168.33.21",
                        "username": "admin",
                        "password": "secret",
                        "record_stream": "main",
                        "enabled": True,
                        "record": True,
                    }
                ],
            }
        )
        panel.write_json(panel.CONFIG_PATH, payload)
        directory = panel.recording_base_dir(payload["cameras"][0], 0)
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("20260727-173000.mp4", "20260727-173010.mp4"):
            (directory / name).write_bytes(b"x" * 2048)
        key = panel.recording_key(payload["cameras"][0], 0)
        day = "2026-07-27"
        cache_dir = panel.recording_cache_dir(key, day)
        cache_dir.mkdir(parents=True, exist_ok=True)
        entries = panel.recording_file_entries(payload["cameras"][0], 0, limit=240, segment_seconds=10, day_key=day)
        signature = panel.recording_cache_source_signature(entries, 10)
        panel.recording_cache_video_path(key, day).write_bytes(b"cached mp4")
        panel.write_json(
            panel.recording_cache_meta_path(key, day),
            {
                "cache_id": "cache-test",
                "cache_name": day,
                "source_hash": signature["hash"],
                "source_count": len(signature["items"]),
                "file_count": 2,
                "total_seconds": 20,
                "built_at": "2026-07-28T12:00:00+0000",
            },
        )

        status = panel.recording_status_payload(payload)["cameras"][0]["playback_cache"]

        self.assertTrue(status["ready"])
        self.assertTrue(status["raw_ready"])
        self.assertTrue(status["current"])
        self.assertFalse(status["too_short_for_sources"])
        self.assertEqual(status["cache_name"], day)
        self.assertEqual(status["total_seconds"], 20)
        self.assertIn(f"recording-cache/{key}/{day}/timeline.mp4", status["url"])

    def test_recording_cache_status_hides_short_stale_cache_when_many_segments_exist(self):
        panel = load_panel_module()
        panel.MIN_RECORDING_FILE_READY_SECONDS = 0
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {"recordings_dir": str(panel.HOME_DIR / "recordings")},
                "nvr": {"segment_seconds": 10},
                "cameras": [
                    {
                        "id": "hikvision_1",
                        "vendor": "hikvision",
                        "host": "192.168.33.21",
                        "username": "admin",
                        "password": "secret",
                        "record_stream": "main",
                        "enabled": True,
                        "record": True,
                    }
                ],
            }
        )
        panel.write_json(panel.CONFIG_PATH, payload)
        directory = panel.recording_base_dir(payload["cameras"][0], 0)
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("20260727-173000.mp4", "20260727-173010.mp4", "20260727-173020.mp4"):
            (directory / name).write_bytes(b"x" * 32)
        key = panel.recording_key(payload["cameras"][0], 0)
        day = "2026-07-27"
        panel.recording_cache_dir(key, day).mkdir(parents=True, exist_ok=True)
        panel.recording_cache_video_path(key, day).write_bytes(b"short cached mp4")
        panel.write_json(
            panel.recording_cache_meta_path(key, day),
            {
                "cache_id": "old-short-cache",
                "cache_name": day,
                "source_hash": "old",
                "source_count": 1,
                "file_count": 1,
                "total_seconds": 7,
                "built_at": "2026-07-28T12:00:00+0000",
            },
        )

        status = panel.recording_status_payload(payload)["cameras"][0]["playback_cache"]

        self.assertFalse(status["ready"])
        self.assertTrue(status["raw_ready"])
        self.assertTrue(status["too_short_for_sources"])
        self.assertEqual(status["url"], "")

    def test_recording_cache_command_creates_faststart_mp4_file(self):
        panel = load_panel_module()
        command = panel.build_recording_cache_command(panel.HOME_DIR / "timeline.ffconcat", panel.HOME_DIR / "timeline.mp4")

        self.assertIn("+faststart", command)
        self.assertIn("-c:v", command)
        self.assertIn("copy", command)
        self.assertIn("-c:a", command)
        self.assertIn("aac", command)
        self.assertIn("aresample=async=1:first_pts=0", command)
        self.assertIn("-f", command)
        self.assertIn("concat", command)

    def test_recording_thumbnail_command_generates_small_jpeg(self):
        panel = load_panel_module()
        command = panel.build_recording_thumbnail_command(panel.HOME_DIR / "input.mp4", panel.HOME_DIR / "thumb.jpg")

        self.assertIn("-frames:v", command)
        self.assertEqual(command[command.index("-frames:v") + 1], "1")
        self.assertIn("scale=480:-2:force_original_aspect_ratio=decrease", command)
        self.assertIn("-vcodec", command)
        self.assertIn("mjpeg", command)
        self.assertIn("thumb.jpg", command[-1])

    def test_ensure_configured_recordings_starts_enabled_record_camera(self):
        panel = load_panel_module()
        payload = panel.normalize_config(
            {
                "server": {},
                "storage": {"recordings_dir": str(panel.HOME_DIR / "recordings")},
                "cameras": [
                    {
                        "id": "hikvision_1",
                        "vendor": "hikvision",
                        "host": "192.168.33.21",
                        "username": "admin",
                        "password": "secret",
                        "record_stream": "main",
                        "enabled": True,
                        "record": True,
                    }
                ],
            }
        )
        calls = []
        original = panel.start_recording

        def fake_start(camera_config, index):
            calls.append((camera_config["id"], index))
            return {"started": True, "status": "recording"}

        panel.start_recording = fake_start
        try:
            results = panel.ensure_configured_recordings(payload, "test")
        finally:
            panel.start_recording = original

        self.assertEqual(calls, [("hikvision_1", 0)])
        self.assertEqual(results[0]["action"], "started")

    def test_autoconfig_recommends_keyframe_and_substream_tuning(self):
        panel = load_panel_module()
        recommendations = panel.camera_autoconfig_recommendations(
            {
                "sub": {
                    "video": {
                        "codec": "H.264",
                        "width": "1920",
                        "height": "1080",
                        "fps": "20",
                        "bitrate": "4096",
                        "keyframe_interval": "20",
                    },
                    "audio": {"codec": "pcm_alaw"},
                }
            },
            [],
        )
        messages = " ".join(item["message"] for item in recommendations)

        self.assertIn("keyframe interval", messages)
        self.assertIn("substream", messages)
        self.assertIn("bitrate", messages)

    def test_recording_command_copies_h264_video_for_low_cpu_recording(self):
        panel = load_panel_module()

        command = panel.build_recording_command(
            "rtsp://admin:secret@192.168.33.21:554/Streaming/Channels/101",
            "/tmp/%Y%m%d-%H%M%S.mp4",
            10,
            "copy_h264",
        )

        self.assertIn("-c:v", command)
        self.assertEqual(command[command.index("-c:v") + 1], "copy")
        self.assertIn("-c:a", command)
        self.assertEqual(command[command.index("-c:a") + 1], "aac")

    def test_recording_command_transcodes_hevc_to_browser_h264(self):
        panel = load_panel_module()

        command = panel.build_recording_command(
            "rtsp://admin:secret@192.168.33.21:554/Streaming/Channels/101",
            "/tmp/%Y%m%d-%H%M%S.mp4",
            10,
            "transcode_to_h264",
        )

        self.assertIn("-c:v", command)
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertIn("-tune", command)
        self.assertEqual(command[command.index("-tune") + 1], "zerolatency")


if __name__ == "__main__":
    unittest.main()
