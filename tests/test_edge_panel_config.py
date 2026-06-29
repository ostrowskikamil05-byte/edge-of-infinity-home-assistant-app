import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "edge-of-infinity" / "rootfs" / "usr" / "share" / "edge-of-infinity" / "edge-panel.py"


def load_panel_module():
    temp_root = Path(tempfile.mkdtemp())
    os.environ["EDGE_HOME_DIR"] = str(temp_root / "home")
    os.environ["EDGE_DATA_DIR"] = str(temp_root / "data")
    os.environ["EDGE_HOME_CONFIG"] = str(temp_root / "home" / "edge.json")
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

    def test_panel_camera_overrides_win_after_external_config_rewrite(self):
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

        self.assertEqual(loaded["cameras"][0]["host"], "192.168.33.21")
        self.assertEqual(loaded["cameras"][0]["username"], "admin")
        self.assertEqual(loaded["cameras"][0]["password"], "secret")
        self.assertEqual(loaded["cameras"][0]["rtsp_main"], "rtsp://admin:secret@192.168.33.21:554/Streaming/Channels/101")
        self.assertEqual(loaded["cameras"][0]["rtsp_sub"], "rtsp://admin:secret@192.168.33.21:554/Streaming/Channels/102")
        self.assertEqual(loaded["cameras"][0]["onvif_url"], "http://192.168.33.21:80/onvif/device_service")
        self.assertEqual(loaded["cameras"][0]["isapi_base_url"], "http://192.168.33.21")
        self.assertTrue(loaded["cameras"][0]["enabled"])
        self.assertTrue(loaded["cameras"][0]["record"])
        self.assertTrue(loaded["cameras"][0]["low_latency"])
        self.assertEqual(loaded["cameras"][0]["snapshot_stream"], "sub")
        self.assertEqual(loaded["cameras"][0]["live_stream"], "main")
        self.assertEqual(loaded["cameras"][0]["tile_stream"], "sub")
        self.assertEqual(loaded["cameras"][0]["record_stream"], "main")

    def test_panel_config_wins_over_stale_runtime_and_override_files(self):
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

        authoritative = json.loads(json.dumps(runtime))
        authoritative["cameras"][0]["snapshot_stream"] = "sub"
        authoritative["cameras"][0]["live_stream"] = "sub"
        authoritative["cameras"][0]["tile_stream"] = "sub"
        authoritative["cameras"][0]["record_stream"] = "sub"
        panel.write_json(panel.PANEL_CONFIG_PATH, authoritative)

        loaded = panel.load_config()

        self.assertEqual(loaded["cameras"][0]["snapshot_stream"], "sub")
        self.assertEqual(loaded["cameras"][0]["live_stream"], "sub")
        self.assertEqual(loaded["cameras"][0]["tile_stream"], "sub")
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

    def test_live_mobile_settings_are_normalized_and_preserved(self):
        panel = load_panel_module()
        payload = {
            "server": {},
            "storage": {},
            "live": {
                "engine": "janus_webrtc",
                "prebuffer_enabled": True,
                "prebuffer_local_ms": 5000,
                "prebuffer_remote_ms": 2500,
                "mobile_webrtc_public_hosts": "edge.example.com,192.168.33.17",
                "mobile_webrtc_stun_url": "stun:stun.l.google.com:19302",
                "mobile_webrtc_turn_url": "turns:turn.example.com:443",
                "mobile_webrtc_turn_username": "edge",
                "mobile_webrtc_turn_password": "secret",
            },
            "cameras": [camera("hikvision_1", "192.168.33.21", "sub")],
        }

        normalized = panel.normalize_config(payload)

        self.assertTrue(normalized["live"]["prebuffer_enabled"])
        self.assertEqual(normalized["live"]["prebuffer_remote_ms"], 2500)
        self.assertEqual(normalized["live"]["mobile_webrtc_public_hosts"], "edge.example.com,192.168.33.17")
        self.assertEqual(normalized["live"]["mobile_webrtc_turn_url"], "turns:turn.example.com:443")
        self.assertEqual(normalized["live"]["mobile_webrtc_turn_password"], "secret")

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
