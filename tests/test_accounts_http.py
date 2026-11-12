"""Exercise real HTTP transport, redirects and TLS without sending real mail."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_accounts import accounts


class AccountTransportTests(unittest.TestCase):
    def test_post_body_redirect_refusal_fixed_errors_and_certificate_validation(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.do_POST()

            def do_POST(self):
                seen.append((self.path, self.rfile.read(int(self.headers.get('Content-Length', '0')))))
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/leak')
                    self.end_headers()
                    return
                status, data = (400, {'error': 'authorization_pending', 'error_description': 'private-provider-value'}) if self.path == '/pending' else (200, {'ok': True})
                payload = json.dumps(data).encode() if self.path != '/large' else b'x' * 131073
                self.send_response(status)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
                'NO_PROXY': 'localhost,127.0.0.1', 'no_proxy': 'localhost,127.0.0.1'}):
            service = accounts.CentralAccounts(folder)
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = 'http://127.0.0.1:' + str(server.server_port)
            try:
                self.assertEqual(service._request(base + '/token', form={'token': 'private + token'}), {'ok': True})
                self.assertEqual(seen[-1], ('/token', b'token=private+%2B+token'))
                self.assertEqual(service._request(base + '/pending', form={}), {'error': 'authorization_pending'})
                with self.assertRaises(accounts.AccountError) as error:
                    service._request(base + '/redirect', token='private-access-token')
                self.assertNotIn('private', str(error.exception))
                self.assertFalse(any(path == '/leak' for path, _ in seen))
                with self.assertRaises(accounts.AccountError):
                    service._request(base + '/large')
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            root = Path(folder)
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', str(root / 'key.pem'), '-out', str(root / 'ca.pem'),
                            '-days', '1', '-subj', '/CN=localhost',
                            '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                           check=True, capture_output=True)
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(root / 'ca.pem', root / 'key.pem')
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = 'https://127.0.0.1:' + str(server.server_port)
            try:
                with self.assertRaises(accounts.AccountError):
                    service._request(base + '/token')
                with patch.dict(os.environ, {'SSL_CERT_FILE': str(root / 'ca.pem')}):
                    self.assertEqual(service._request(base + '/token'), {'ok': True})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
