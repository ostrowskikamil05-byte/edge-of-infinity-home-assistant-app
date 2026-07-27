import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = (
    ROOT
    / "edge-of-infinity"
    / "rootfs"
    / "usr"
    / "share"
    / "edge-of-infinity"
    / "custom_components"
    / "edge"
    / "client.py"
)


def load_client_module():
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientError = type("ClientError", (Exception,), {})
    aiohttp.ClientResponseError = type("ClientResponseError", (aiohttp.ClientError,), {})
    aiohttp.ClientSession = type("ClientSession", (), {})
    sys.modules.setdefault("aiohttp", aiohttp)
    spec = importlib.util.spec_from_file_location("edge_client_test", CLIENT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class EdgeIntegrationClientTests(unittest.TestCase):
    def test_config_fields_override_stale_camera_status(self):
        client = load_client_module()
        status = [
            {
                "id": "hikvision_1",
                "name": "Hikvision 1",
                "host": "192.168.33.21",
                "status": "online",
                "rtsp_main": "rtsp://admin:secret@192.168.33.21:554/Streaming/Channels/101",
                "rtsp_sub": "rtsp://admin:secret@192.168.33.21:554/Streaming/Channels/102",
                "live_stream": "main",
            }
        ]
        config = [
            {
                "id": "hikvision_1",
                "name": "Hikvision 1",
                "host": "192.168.33.136",
                "vendor": "hikvision",
                "rtsp_main": "rtsp://admin:secret@192.168.33.136:554/Streaming/Channels/101",
                "rtsp_sub": "rtsp://admin:secret@192.168.33.136:554/Streaming/Channels/102",
                "live_stream": "sub",
                "tile_stream": "sub",
                "record_stream": "main",
                "snapshot_stream": "sub",
            }
        ]

        merged = client._normalize_cameras(client._merge_camera_config(status, config))

        self.assertEqual(merged[0]["host"], "192.168.33.136")
        self.assertEqual(merged[0]["status"], "online")
        self.assertEqual(merged[0]["live_stream"], "sub")
        self.assertEqual(merged[0]["live_rtsp"], "rtsp://admin:secret@192.168.33.136:554/Streaming/Channels/102")


if __name__ == "__main__":
    unittest.main()
