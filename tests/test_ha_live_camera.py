import asyncio
from contextlib import contextmanager
from enum import IntFlag
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_edge_integration_client import load_client_module
from test_edge_panel_config import load_panel_module

ROOT = Path(__file__).resolve().parents[1]
CAMERA_PATH = ROOT / 'edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/camera.py'


@contextmanager
def native_module():
    modules = {}
    for name in ('homeassistant', 'homeassistant.components', 'homeassistant.components.camera', 'homeassistant.components.ffmpeg', 'homeassistant.config_entries', 'homeassistant.core', 'homeassistant.helpers', 'homeassistant.helpers.entity_platform', 'homeassistant.helpers.update_coordinator', 'edge_native_test', 'edge_native_test.const', 'edge_native_test.coordinator'):
        modules[name] = types.ModuleType(name)
    class Features(IntFlag):
        STREAM = 2
    class Camera:
        def __init__(self):
            self.stream = None
            self.stream_options = {}
            self.entity_id = 'camera.actual_front_door'
    class CoordinatorEntity:
        @classmethod
        def __class_getitem__(cls, item):
            return cls
        def __init__(self, coordinator):
            self.coordinator = coordinator
            self.hass = coordinator.hass
        async def async_added_to_hass(self):
            pass
        async def async_will_remove_from_hass(self):
            pass
        def _handle_coordinator_update(self):
            pass
    modules['homeassistant.components.camera'].Camera = Camera
    modules['homeassistant.components.camera'].CameraEntityFeature = Features
    modules['homeassistant.components.ffmpeg'].async_get_image = AsyncMock(return_value=b'jpeg-frame')
    modules['homeassistant.config_entries'].ConfigEntry = object
    modules['homeassistant.core'].HomeAssistant = object
    modules['homeassistant.helpers.entity_platform'].AddEntitiesCallback = object
    modules['homeassistant.helpers.update_coordinator'].CoordinatorEntity = CoordinatorEntity
    modules['edge_native_test.const'].DOMAIN = 'edge'
    modules['edge_native_test.coordinator'].EdgeCoordinator = object
    modules['edge_native_test.client'] = load_client_module()
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location('edge_native_test.camera', CAMERA_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module, modules['homeassistant.components.ffmpeg'].async_get_image


class NativeCameraTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_source_and_refresh_use_private_url_only_in_worker(self):
        with native_module() as (module, get_image):
            with tempfile.TemporaryDirectory() as folder:
                tasks = []
                async def execute(function, *args):
                    return function(*args)
                def schedule(coro):
                    task = asyncio.create_task(coro)
                    tasks.append(task)
                    return task
                source = {'id': 'front', 'name': 'Front door', 'enabled': True, 'status': 'online', 'vendor': 'reolink', 'live_stream': 'sub', 'snapshot_stream': 'sub', 'rtsp_sub': 'rtsp://user:secret@192.0.2.1/sub'}
                hass = types.SimpleNamespace(data={'edge': {'entry': {'client': types.SimpleNamespace(is_local=True)}}}, config=types.SimpleNamespace(path=lambda *parts: str(Path(folder).joinpath(*parts))), async_add_executor_job=execute, async_create_task=schedule)
                coordinator = types.SimpleNamespace(hass=hass, config_entry=types.SimpleNamespace(entry_id='entry'), data={'cameras': [source]}, client=types.SimpleNamespace(camera_image=AsyncMock(return_value=None)))
                entity = module.EdgeCamera(coordinator, source)
                await entity.async_added_to_hass()
                self.assertEqual(await entity.stream_source(), source['rtsp_sub'])
                self.assertTrue(entity._attr_supported_features & 2)
                self.assertEqual(await entity.async_camera_image(320, 180), b'jpeg-frame')
                get_image.assert_awaited_once_with(hass, source['rtsp_sub'], width=320, height=180)
                mapping = json.loads(Path(folder, 'edge/ha-entities.json').read_text())
                self.assertEqual(mapping['entities'][0]['entity_id'], 'camera.actual_front_door')
                self.assertNotIn('secret', json.dumps(mapping))
                self.assertNotIn('rtsp://', json.dumps(mapping))
                entity.stream = Mock()
                coordinator.data['cameras'][0] = {**source, 'name': 'New name', 'rtsp_sub': 'rtsp://user:new-secret@192.0.2.2/sub'}
                entity._handle_coordinator_update()
                entity.stream.update_source.assert_called_once_with('rtsp://user:new-secret@192.0.2.2/sub')
                await asyncio.gather(*tasks)
                await entity.async_will_remove_from_hass()
                self.assertEqual(json.loads(Path(folder, 'edge/ha-entities.json').read_text())['entities'], [])

    async def test_new_cameras_are_added_after_coordinator_updates(self):
        with native_module() as (module, _):
            coordinator = types.SimpleNamespace(data={'cameras': [{'id': 'one', 'name': 'One'}]}, hass=None, async_add_listener=Mock(return_value=lambda: None))
            hass = types.SimpleNamespace(data={'edge': {'entry': {'coordinator': coordinator}}})
            entry = types.SimpleNamespace(entry_id='entry', async_on_unload=Mock())
            added = []
            await module.async_setup_entry(hass, entry, lambda entities: added.extend(entities))
            self.assertEqual([entity._camera_id for entity in added], ['one'])
            coordinator.data['cameras'].append({'id': 'two', 'name': 'Two'})
            listener = coordinator.async_add_listener.call_args.args[0]
            listener()
            listener()
            self.assertEqual([entity._camera_id for entity in added], ['one', 'two'])


class StreamSourceTests(unittest.TestCase):
    def test_source_selection_does_not_return_redacted_or_disabled_streams(self):
        client = load_client_module()
        self.assertEqual(client.camera_stream_source({'live_stream': 'sub', 'rtsp_sub': 'rtsp://user:secret@192.0.2.1/sub'}), 'rtsp://user:secret@192.0.2.1/sub')
        self.assertIsNone(client.camera_stream_source({'rtsp_sub': 'rtsp://user:***@192.0.2.1/sub'}))
        self.assertIsNone(client.camera_stream_source({'rtsp_sub': 'rtsp://user:%2A%2A%2A@192.0.2.1/sub'}))
        self.assertIsNone(client.camera_stream_source({'enabled': False, 'rtsp_sub': 'rtsp://192.0.2.1/sub'}))
        self.assertIsNone(client.camera_stream_source({'rtsp_sub': 'https://example.com/video'}))
        self.assertEqual(client.camera_stream_source({'rtsp_main': 'rtsps://192.0.2.1/main'}), 'rtsps://192.0.2.1/main')


class DashboardMetadataTests(unittest.TestCase):
    def test_exact_registered_entity_is_used_without_exposing_credentials(self):
        p = load_panel_module()
        p.commit_panel_config({'cameras': [{'id': 'front', 'name': 'Front door', 'host': '192.0.2.1', 'username': 'private-user', 'password': 'private-secret'}]})
        p.write_json(p.HOME_DIR / 'ha-entities.json', {'entities': [{'camera_id': 'front', 'entity_id': 'camera.remotely_named', 'local': False}, {'camera_id': 'front', 'entity_id': 'camera.renamed_by_user', 'local': True}]})
        payload = p.home_assistant_dashboard_payload()
        self.assertEqual(payload['cameras'][0]['entity_id'], 'camera.renamed_by_user')
        self.assertTrue(payload['integration_detected'])
        for private in ('private-user', 'private-secret', '192.0.2.1', 'rtsp://'):
            self.assertNotIn(private, json.dumps(payload))

    def test_missing_or_invalid_map_requires_an_actual_entity_id(self):
        p = load_panel_module()
        p.commit_panel_config({'cameras': [{'id': 'front', 'host': '192.0.2.1'}]})
        for mapping in ([], {}, {'entities': [{'camera_id': 'front', 'entity_id': 'camera.foo\npassword: secret'}]}):
            p.write_json(p.HOME_DIR / 'ha-entities.json', mapping)
            self.assertEqual(p.home_assistant_dashboard_payload()['cameras'][0]['entity_id'], '')
