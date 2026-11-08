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
    modules['edge_native_test.const'].CARD_VERSION = '0.11.11'
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
                source = {'id': 'front', 'name': 'Front door', 'enabled': True, 'status': 'online', 'vendor': 'reolink', 'live_stream': 'sub', 'snapshot_stream': 'sub', 'rtsp_sub': 'rtsp://user:secret@192.0.2.1/sub', 'edge_restream': {'nvr': 'rtsp://edge-addon:8556/front_main', 'main': 'rtsp://edge-addon:8556/front_main', 'sub': 'rtsp://edge-addon:8556/front_sub'}}
                hass = types.SimpleNamespace(data={'edge': {'entry': {'client': types.SimpleNamespace(is_local=True)}}}, config=types.SimpleNamespace(path=lambda *parts: str(Path(folder).joinpath(*parts))), async_add_executor_job=execute, async_create_task=schedule)
                coordinator = types.SimpleNamespace(hass=hass, config_entry=types.SimpleNamespace(entry_id='entry'), data={'cameras': [source]}, client=types.SimpleNamespace(camera_image=AsyncMock(return_value=None)))
                entity = module.EdgeCamera(coordinator, source)
                await entity.async_added_to_hass()
                self.assertEqual(await entity.stream_source(), source['edge_restream']['nvr'])
                self.assertTrue(entity._attr_supported_features & 2)
                self.assertEqual(await entity.async_camera_image(320, 180), b'jpeg-frame')
                get_image.assert_awaited_once_with(hass, source['edge_restream']['nvr'], width=320, height=180)
                mapping = json.loads(Path(folder, 'edge/ha-entities.json').read_text())
                self.assertEqual(mapping['entities'][0]['entity_id'], 'camera.actual_front_door')
                self.assertNotIn('secret', json.dumps(mapping))
                self.assertNotIn('rtsp://', json.dumps(mapping))
                entity.stream = Mock()
                coordinator.data['cameras'][0] = {**source, 'name': 'New name', 'rtsp_sub': 'rtsp://user:new-secret@192.0.2.2/sub', 'edge_restream': {'nvr': 'rtsp://edge-addon:8557/front_main'}}
                entity._handle_coordinator_update()
                entity.stream.update_source.assert_called_once_with('rtsp://edge-addon:8557/front_main')
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


class CardRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_registers_real_bundle_once_with_cache_busting(self):
        modules = {}
        for name in ('homeassistant', 'homeassistant.components', 'homeassistant.components.frontend', 'homeassistant.components.http', 'homeassistant.core', 'edge_card_test', 'edge_card_test.const'):
            modules[name] = types.ModuleType(name)
        from dataclasses import dataclass
        @dataclass
        class StaticPathConfig:
            url_path: str
            path: str
            cache_headers: bool
        modules['homeassistant.components.http'].StaticPathConfig = StaticPathConfig
        modules['homeassistant.core'].HomeAssistant = object
        register_js = modules['homeassistant.components.frontend'].add_extra_js_url = Mock()
        modules['edge_card_test.const'].DOMAIN = 'edge'
        modules['edge_card_test.const'].CARD_VERSION = '0.11.11'
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location('edge_card_test.frontend', CAMERA_PATH.with_name('frontend.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            hass = types.SimpleNamespace(data={}, http=types.SimpleNamespace(async_register_static_paths=AsyncMock()))
            await module.async_register_card(hass)
            await module.async_register_card(hass)
            hass.http.async_register_static_paths.assert_awaited_once()
            resource = hass.http.async_register_static_paths.call_args.args[0][0]
            self.assertEqual(resource.url_path, '/edge/edge-live-card.js')
            self.assertTrue(Path(resource.path).is_file())
            self.assertFalse(resource.cache_headers)
            register_js.assert_called_once_with(hass, '/edge/edge-live-card.js?v=0.11.11')

    async def test_exports_card_capability_without_secrets(self):
        p = load_panel_module()
        p.commit_panel_config({'cameras': [{'id': 'front', 'host': '192.0.2.1', 'password': 'secret'}]})
        p.write_json(p.HOME_DIR / 'ha-entities.json', {'card_type': 'custom:edge-live-card', 'card_version': '0.11.11', 'entities': []})
        payload = p.home_assistant_dashboard_payload()
        self.assertTrue(payload['card_ready'])
        self.assertEqual(payload['card_version'], '0.11.11')
        self.assertEqual(payload['card_resource'], '/edge/edge-live-card.js')
        self.assertNotIn('secret', json.dumps(payload))
