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
        raw_payload["cameras"][1]["snapshot_stream"] = "main"

        merged_payload = panel.merge_existing_camera_values(raw_payload)
        normalized_payload = panel.normalize_config(merged_payload)
        saved_payload = panel.preserve_submitted_stream_choices(
            json.loads(json.dumps(normalized_payload)),
            merged_payload,
            raw_payload,
        )
        panel.write_json(panel.CONFIG_PATH, saved_payload)
        loaded_payload = panel.preserve_submitted_stream_choices(panel.load_config(), merged_payload, raw_payload)

        self.assertEqual(loaded_payload["cameras"][0]["snapshot_stream"], "sub")
        self.assertEqual(loaded_payload["cameras"][0]["live_stream"], "main")
        self.assertEqual(loaded_payload["cameras"][0]["tile_stream"], "sub")
        self.assertEqual(loaded_payload["cameras"][0]["record_stream"], "main")
        self.assertEqual(loaded_payload["cameras"][1]["snapshot_stream"], "main")


if __name__ == "__main__":
    unittest.main()
