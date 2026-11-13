"""UI metadata and cacheable local fonts must not change camera transports."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_edge_panel_config import camera, load_panel_module


@contextmanager
def server(panel):
    with ThreadingHTTPServer(('127.0.0.1',0),panel.EdgeHandler) as http:
        worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
        try:yield 'http://127.0.0.1:'+str(http.server_port)
        finally:http.shutdown();worker.join(timeout=2)


class UIAssetsTests(unittest.TestCase):
    def test_bundled_font_is_cacheable_and_available_during_setup(self):
        panel=load_panel_module({'id':'f'*32,'setup_complete':False})
        with server(panel) as base:
            with urlopen(base+'/api/ui/fonts/inter-latin.woff2?v='+panel.APP_VERSION) as response:
                data=response.read();etag=response.headers['ETag']
                self.assertTrue(data.startswith(b'wOF2'))
                self.assertEqual(response.headers.get_content_type(),'font/woff2')
                self.assertIn('immutable',response.headers['Cache-Control'])
                self.assertNotEqual(response.headers['Pragma'],'no-cache')
            with self.assertRaises(HTTPError) as cached:urlopen(Request(base+'/api/ui/fonts/inter-latin.woff2?v='+panel.APP_VERSION,headers={'If-None-Match':etag}))
            self.assertEqual(cached.exception.code,304)
            with self.assertRaises(HTTPError) as locked:urlopen(base+'/api/config')
            self.assertEqual(locked.exception.code,423)

    def test_font_allowlist_rejects_config_paths(self):
        from edge_ui_assets import ui_font
        for name in ('../edge.json','/homeassistant/edge/edge.json','other.woff2'):
            with self.assertRaises(ValueError):ui_font(name)

    def test_camera_group_is_bounded_and_does_not_change_streams(self):
        panel=load_panel_module();raw=camera('front','192.0.2.10','main')
        baseline=panel.normalize_camera(raw,1)
        grouped=panel.normalize_camera({**raw,'group':' Ogród\x00 / Wejście '+'x'*80},1)
        self.assertLessEqual(len(grouped['group']),64)
        self.assertNotIn('\x00',grouped['group'])
        self.assertEqual({k:v for k,v in grouped.items() if k!='group'},{k:v for k,v in baseline.items() if k!='group'})
