import asyncio
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_edge_integration_client import load_client_module
from test_edge_panel_config import load_panel_module, camera
from test_ha_live_camera import native_module


class RestreamMetadataTests(unittest.TestCase):
    def test_discovers_internal_addon_dns_and_exports_only_proxy_paths(self):
        p = load_panel_module()
        p.MEDIAMTX_ENABLED = True
        config = p.normalize_config({'cameras': [camera('front door', '192.0.2.9', 'sub')]})
        with patch.dict(p.os.environ, {'SUPERVISOR_TOKEN': 'test-token', 'EDGE_HA_RTSP_HOST': ''}), patch.object(p, 'supervisor_self_request', return_value={'hostname': 'abcd-edge-of-infinity'}) as request:
            payload = p.home_assistant_restream_payload(config)
            p.home_assistant_restream_payload(config)
        request.assert_called_once_with('/addons/self/info')
        paths = payload['cameras'][0]['streams']
        self.assertEqual(paths['nvr'], paths['main'])
        self.assertEqual(paths['sub'], 'rtsp://abcd-edge-of-infinity:8556/front_door_sub')
        for private in ('secret', 'admin', '192.0.2.9', 'test-token'):
            self.assertNotIn(private, json.dumps(payload))
        config['cameras'][0]['record_stream'] = 'sub'
        self.assertEqual(p.home_assistant_restream_payload(config)['cameras'][0]['streams']['nvr'], paths['sub'])

    def test_disabled_or_missing_profiles_do_not_advertise_direct_camera(self):
        p = load_panel_module()
        p._HA_RESTREAM_HOST = 'edge-addon'
        config = {'cameras': [{'id': 'front', 'enabled': True, 'record_stream': 'sub', 'rtsp_main': 'rtsp://user:secret@192.0.2.1/main'}]}
        self.assertEqual(p.home_assistant_restream_payload(config)['cameras'][0]['streams']['nvr'], 'rtsp://edge-addon:8556/front_main')
        self.assertNotIn('sub', p.home_assistant_restream_payload(config)['cameras'][0]['streams'])
        p.MEDIAMTX_ENABLED = False
        self.assertFalse(p.home_assistant_restream_payload(config)['available'])
        self.assertEqual(p.home_assistant_restream_payload(config)['cameras'][0]['streams'], {'nvr': ''})

    def test_status_probes_share_the_recording_proxy(self):
        p = load_panel_module()
        config = p.normalize_config({'cameras': [camera('front', '192.0.2.9', 'sub')]})
        config['cameras'][0]['live_stream'] = 'sub'
        p.commit_panel_config(config)
        p.MEDIAMTX_ENABLED = True
        with patch.object(p, 'probe_rtsp_stream', return_value={'video': {'codec_name': 'h264'}}) as probe:
            p.refresh_status()
        self.assertEqual([call.args[0] for call in probe.call_args_list], ['rtsp://127.0.0.1:8556/front_main', 'rtsp://127.0.0.1:8556/front_sub'])
        self.assertEqual(p.recording_source_stream(config['cameras'][0], 0, 'main')[0], probe.call_args_list[0].args[0])


class RestreamClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_loads_backend_only_nvr_metadata_and_rejects_missing_metadata(self):
        module = load_client_module()
        client = module.EdgeClient('local', object())
        rows = [{'id': 'front', 'enabled': True, 'rtsp_main': 'rtsp://user:secret@192.0.2.1/main'}]
        with patch.object(module.EdgeClient, "_read_local_json", AsyncMock(return_value={'available': True, 'cameras': [{'camera_id': 'front', 'streams': {'nvr': 'rtsp://edge-addon:8556/front_main'}}]})) as read_json:
            result = await client._with_restreams(rows)
            self.assertEqual(module.camera_nvr_source(result[0]), 'rtsp://edge-addon:8556/front_main')
            self.assertIsNone(module.camera_nvr_source(result[0], 'sub'))
            for payload in ({}, [], None, {'available': True, 'cameras': None}):
                read_json.return_value = payload
                result = await client._with_restreams(rows)
                self.assertIsNone(module.camera_nvr_source(result[0]))
            read_json.side_effect = module.EdgeConnectionError('not yet installed')
            self.assertIsNone(module.camera_nvr_source((await client._with_restreams(rows))[0]))

    async def test_registers_all_profiles_and_publishes_actual_renamed_entities(self):
        with native_module() as (module, _):
            with tempfile.TemporaryDirectory() as folder:
                source = {'id': 'front', 'name': 'Front', 'enabled': True, 'status': 'online', 'rtsp_main': 'rtsp://user:secret@192.0.2.1/main', 'rtsp_sub': 'rtsp://user:secret@192.0.2.1/sub', 'edge_restream': {'nvr': 'rtsp://edge-addon:8556/front_main', 'main': 'rtsp://edge-addon:8556/front_main', 'sub': 'rtsp://edge-addon:8556/front_sub'}}
                async def execute(func, *args):
                    return func(*args)
                entry = types.SimpleNamespace(entry_id='entry', async_on_unload=Mock())
                coordinator = types.SimpleNamespace(data={'cameras': [source]}, config_entry=entry, async_add_listener=Mock(return_value=lambda: None))
                hass = types.SimpleNamespace(data={'edge': {'entry': {'coordinator': coordinator, 'client': types.SimpleNamespace(is_local=True)}}}, config=types.SimpleNamespace(path=lambda *parts: str(Path(folder).joinpath(*parts))), async_add_executor_job=execute)
                coordinator.hass = hass
                added = []
                await module.async_setup_entry(hass, entry, lambda entities: added.extend(entities))
                self.assertEqual([entity._stream_role for entity in added], ['nvr', 'main', 'sub', 'talk'])
                self.assertEqual(added[0]._attr_unique_id, 'edge_front')
                self.assertEqual(len({entity._attr_unique_id for entity in added}), 4)
                ids = ['camera.renamed_front', 'camera.high_custom', 'camera.low_custom', 'camera.speaker_custom']
                for entity, entity_id in zip(added, ids):
                    entity.entity_id = entity_id
                    await entity.async_added_to_hass()
                self.assertEqual(await added[0].stream_source(), await added[1].stream_source())
                self.assertEqual(await added[2].stream_source(), source['edge_restream']['sub'])
                self.assertEqual(await added[3].stream_source(), source['rtsp_sub'])
                self.assertTrue(added[0].available)
                attrs = added[0].extra_state_attributes
                self.assertEqual(attrs['edge_stream_entities'], dict(zip(['nvr', 'main', 'sub', 'talk'], ids)))
                self.assertEqual(attrs['edge_stream_source'], 'edge_nvr')
                mapping = json.loads(Path(folder, 'edge/ha-entities.json').read_text())
                self.assertEqual(mapping['entities'][0]['entity_id'], ids[0])
                self.assertEqual(mapping['entities'][0]['streams']['sub'], ids[2])
                self.assertNotIn('rtsp://', json.dumps(mapping))
                self.assertNotIn('secret', json.dumps(attrs))
                source['edge_restream'] = {}
                self.assertIsNone(await added[0].stream_source())
                self.assertFalse(added[0].available)
                self.assertEqual(await added[3].stream_source(), source['rtsp_sub'])
                await added[2].async_will_remove_from_hass()
                mapping = json.loads(Path(folder, 'edge/ha-entities.json').read_text())
                self.assertNotIn('sub', mapping['entities'][0]['streams'])
