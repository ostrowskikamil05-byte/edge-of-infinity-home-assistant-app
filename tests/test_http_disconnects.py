import io
import unittest
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


if __name__ == '__main__':
    unittest.main()
