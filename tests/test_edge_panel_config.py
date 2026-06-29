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


if __name__ == "__main__":
    unittest.main()
