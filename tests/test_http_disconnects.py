import io
import os
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from test_edge_panel_config import load_panel_module


class DroppedResponse(io.BytesIO):
    def __init__(self, error, fail_on_write):
        super().__init__()
        self.error = error
        self.fail_on_write = fail_on_write
        self.writes = 0

    def write(self, data):
        self.writes += 1
        if self.writes == self.fail_on_write:
            raise self.error('client disconnected')
        return super().write(data)


class HttpDisconnectTests(unittest.TestCase):
    def setUp(self):
        self.panel = load_panel_module()

    def test_new_direct_representation_is_versioned_and_old_tabs_keep_remux_bytes(self):
        handler=self.panel.EdgeHandler.__new__(self.panel.EdgeHandler);source=Path('/unused.mp4')
        for value,direct,modern in [('hvc1-clock-v4',False,False),('direct-clock-v5',True,False),('aac-clock-v6',True,True)]:
            handler.path='/recordings/front/clip.mp4?compat=1&container='+value
            with patch.object(self.panel.RECORDING_COMPAT,'prepare',return_value=source) as prepare:
                self.assertEqual(handler.compatible_recording(source),source)
                prepare.assert_called_once_with(source,allow_passthrough=direct,allow_small_offset=modern,legacy=not modern)

    def handler(self, output):
        handler = self.panel.EdgeHandler.__new__(self.panel.EdgeHandler)
        handler.rfile = io.BytesIO(b'GET /api/recording/status HTTP/1.1\r\nHost: test\r\n\r\n')
        handler.wfile = output
        handler.client_address = ('127.0.0.1', 1234)
        handler.close_connection = False
        return handler

    def request(self, handler):
        with patch.object(self.panel, 'load_config', return_value={}), patch.object(self.panel, 'schedule_recording_ensure'), patch.object(self.panel, 'recording_status_payload', return_value={'cameras': []}), patch.object(handler, 'log_message'):
            handler.handle_one_request()

    def test_disconnect_during_status_headers_or_body_closes_request(self):
        for error in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            for write in (1, 2):
                with self.subTest(error=error.__name__, write=write):
                    handler = self.handler(DroppedResponse(error, write))
                    self.request(handler)
                    self.assertTrue(handler.close_connection)

    def test_successful_status_response_is_unchanged(self):
        output = io.BytesIO()
        self.request(self.handler(output))
        self.assertIn(b'200 OK\r\n', output.getvalue())
        self.assertTrue(output.getvalue().endswith(b'{"cameras": []}'))

    def test_other_io_failures_are_not_hidden(self):
        with self.assertRaisesRegex(OSError, 'client disconnected'):
            self.request(self.handler(DroppedResponse(OSError, 2)))

    def test_mp4_suffix_range_and_invalid_range_follow_http_byte_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'clip.mp4';target.write_bytes(bytes(range(100)))
            for value,expected in [('bytes=-10',bytes(range(90,100))),('bytes=0-1',bytes(range(2))),('bytes=95-',bytes(range(95,100)))]:
                output=io.BytesIO();handler=self.handler(output);handler.command='GET';handler.requestline='GET /clip.mp4 HTTP/1.1';handler.request_version='HTTP/1.1';handler.headers={'Range':value}
                with patch.object(handler,'log_message'):
                    handler.serve_mp4_file(target,'/clip.mp4','clip.mp4','test',{})
                header,body=output.getvalue().split(b'\r\n\r\n',1)
                self.assertIn(b'206 Partial Content',header);self.assertEqual(body,expected)
            for value in ('bytes=-0','bytes=100-','bytes=20-10'):
                handler=self.handler(io.BytesIO());handler.headers={'Range':value}
                with patch.object(handler,'send_json') as send:handler.serve_mp4_file(target,'/clip.mp4','clip.mp4','test',{})
                self.assertEqual(send.call_args.args[1],416);self.assertEqual(send.call_args.args[2]['Content-Range'],'bytes */100')

    def test_active_mp4_response_survives_cache_eviction_after_headers_start(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'clip.mp4';target.write_bytes(b'active recording bytes')
            output=io.BytesIO();handler=self.handler(output);handler.command='GET';handler.requestline='GET /clip.mp4 HTTP/1.1';handler.request_version='HTTP/1.1';handler.headers={}
            send=handler.send_response
            def evict(status):target.unlink();send(status)
            with patch.object(handler,'log_message'),patch.object(handler,'send_response',side_effect=evict):
                handler.serve_mp4_file(target,'/clip.mp4','clip.mp4','test',{})
            self.assertTrue(output.getvalue().endswith(b'active recording bytes'))

    def test_mp4_validators_allow_range_reuse_and_reject_stale_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'clip.mp4';target.write_bytes(bytes(range(100)))
            def get(headers):
                output=io.BytesIO();handler=self.handler(output);handler.command='GET';handler.requestline='GET /clip.mp4 HTTP/1.1';handler.request_version='HTTP/1.1';handler.headers=headers
                with patch.object(handler,'log_message'):handler.serve_mp4_file(target,'/clip.mp4','clip.mp4','test',{})
                head,body=output.getvalue().split(b'\r\n\r\n',1)
                return head,{k.lower():v.strip() for k,v in (line.split(':',1) for line in head.decode().split('\r\n')[1:] if ':' in line)},body
            _,headers,_=get({'Range':'bytes=0-1'});etag=headers['etag'];date=headers['last-modified']
            for validator in (etag,date):
                head,_,body=get({'Range':'bytes=20-29','If-Range':validator});self.assertIn(b'206 Partial',head);self.assertEqual(body,bytes(range(20,30)))
            head,_,body=get({'If-None-Match':etag});self.assertIn(b'304 Not Modified',head);self.assertEqual(body,b'')
            stat=target.stat();target.write_bytes(b'x'*100);os.utime(target,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
            head,after,body=get({'Range':'bytes=20-29','If-Range':etag});self.assertIn(b'200 OK',head);self.assertNotEqual(etag,after['etag']);self.assertEqual(body,b'x'*100)
            self.assertIn('private',after['cache-control'])

    def test_immutable_compatibility_copy_lru_touch_keeps_etag(self):
        root=self.panel.RECORDING_COMPAT.directory;root.mkdir(parents=True,exist_ok=True);target=root/(64*'a'+'.mp4');target.write_bytes(b'cache')
        tags=[]
        for _ in range(2):
            output=io.BytesIO();handler=self.handler(output);handler.command='GET';handler.requestline='GET /clip.mp4 HTTP/1.1';handler.request_version='HTTP/1.1';handler.headers={}
            with patch.object(handler,'log_message'):handler.serve_mp4_file(target,'/clip.mp4','clip.mp4','test',{})
            tags.append(next(line for line in output.getvalue().split(b'\r\n') if line.startswith(b'ETag:')));os.utime(target,None)
        self.assertEqual(*tags)


if __name__ == '__main__':
    unittest.main()
