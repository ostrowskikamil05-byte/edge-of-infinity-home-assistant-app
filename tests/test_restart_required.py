import asyncio
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

COMPONENT = Path(__file__).resolve().parents[1] / 'edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge'


def load_module(card_version='1.0.0'):
    names = ('homeassistant', 'homeassistant.helpers', 'homeassistant.helpers.issue_registry', 'homeassistant.helpers.event', 'edge_restart_test', 'edge_restart_test.const')
    modules = {name: types.ModuleType(name) for name in names}
    registry = modules['homeassistant.helpers.issue_registry']
    registry.async_create_issue = Mock()
    registry.async_delete_issue = Mock()
    registry.IssueSeverity = types.SimpleNamespace(WARNING='warning')
    modules['homeassistant.helpers'].issue_registry = registry
    modules['homeassistant.helpers.event'].async_track_time_interval = Mock(return_value='unsubscribe')
    modules['edge_restart_test.const'].DOMAIN = 'edge'
    modules['edge_restart_test.const'].CARD_VERSION = card_version
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location('edge_restart_test.restart_check', COMPONENT / 'restart_check.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module, modules


class RestartRequiredTests(unittest.IsolatedAsyncioTestCase):
    async def run_check(self, on_disk, running='1.0.0'):
        module, modules = load_module(running)
        registry = modules['homeassistant.helpers.issue_registry']
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / 'manifest.json'
            if on_disk is not None:
                manifest.write_text(on_disk, encoding='utf-8')
            module.MANIFEST = manifest
            original = module.installed_version
            module.installed_version = lambda: original(manifest)

            async def execute(function, *args):
                return function(*args)
            hass = types.SimpleNamespace(async_add_executor_job=execute)
            with patch.dict(sys.modules, modules):
                unsubscribe, check = module.async_watch_installed_version(hass)
                await check()
            self.assertEqual(unsubscribe, 'unsubscribe')
            interval = modules['homeassistant.helpers.event'].async_track_time_interval.call_args.args
            self.assertIs(interval[0], hass)
            self.assertEqual(interval[2].total_seconds(), 60)
        return registry

    async def test_newer_files_on_disk_ask_for_a_restart(self):
        registry = await self.run_check(json.dumps({'version': '1.1.0'}))
        registry.async_create_issue.assert_called_once()
        args, options = registry.async_create_issue.call_args
        self.assertEqual(args[1:], ('edge', 'restart_required'))
        self.assertEqual(options['translation_placeholders'], {'installed': '1.1.0', 'running': '1.0.0'})
        self.assertEqual(options['translation_key'], 'restart_required')
        self.assertFalse(options['is_fixable'])
        self.assertFalse(options['is_persistent'], 'the hint must disappear by itself after the restart')
        registry.async_delete_issue.assert_not_called()

    async def test_matching_files_clear_the_hint(self):
        registry = await self.run_check(json.dumps({'version': '1.0.0'}))
        registry.async_create_issue.assert_not_called()
        registry.async_delete_issue.assert_called_once()
        self.assertEqual(registry.async_delete_issue.call_args.args[1:], ('edge', 'restart_required'))

    async def test_files_being_replaced_are_not_reported(self):
        for content in (None, '', '{"version"', '[]', '{}'):
            registry = await self.run_check(content)
            registry.async_create_issue.assert_not_called()
            registry.async_delete_issue.assert_not_called()

    def test_running_version_matches_the_shipped_manifest_and_cards(self):
        manifest = json.loads((COMPONENT / 'manifest.json').read_text())['version']
        const = re.search(r'CARD_VERSION = "([^"]+)"', (COMPONENT / 'const.py').read_text())[1]
        cards = re.search(r'EDGE_DASH_VERSION = "([^"]+)"', (COMPONENT / 'www/edge-dashboard-cards.js').read_text())[1]
        self.assertEqual(manifest, const)
        self.assertEqual(cards, const, 'the cards compare their own version with the running integration')

    def test_every_language_explains_the_restart(self):
        for path in sorted((COMPONENT / 'translations').glob('*.json')):
            issue = json.loads(path.read_text(encoding='utf-8'))['issues']['restart_required']
            self.assertTrue(issue['title'].strip(), path.name)
            for placeholder in ('{installed}', '{running}'):
                self.assertIn(placeholder, issue['description'], path.name)

    def test_integration_ships_its_brand_images(self):
        # Home Assistant 2026.3+ serves custom_components/<domain>/brand/ for Repairs,
        # the integration list and the setup dialog.
        def size(name):
            data = (COMPONENT / 'brand' / name).read_bytes()
            self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n', name)
            self.assertLess(len(data), 400 * 1024, name)
            return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
        self.assertEqual(size('icon.png'), (256, 256))
        self.assertEqual(size('icon@2x.png'), (512, 512))
        for name, short in (('logo.png', 256), ('dark_logo.png', 256), ('logo@2x.png', 512), ('dark_logo@2x.png', 512)):
            self.assertEqual(min(size(name)), short, name)
        self.assertEqual({path.name for path in (COMPONENT / 'brand').iterdir()},
                         {'icon.png', 'icon@2x.png', 'logo.png', 'logo@2x.png', 'dark_logo.png', 'dark_logo@2x.png'})

    def test_setup_survives_a_missing_helper(self):
        source = (COMPONENT / '__init__.py').read_text()
        guarded = source[source.index('    try:\n        from .restart_check'):source.index('    return True')]
        self.assertIn('except Exception', guarded)


if __name__ == '__main__':
    unittest.main()
