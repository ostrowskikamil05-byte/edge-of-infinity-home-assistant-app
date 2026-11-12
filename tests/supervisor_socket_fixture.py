"""Disposable Supervisor self-options server for a real s6 container boot."""
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socketserver
import sys


root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
state_path = root / 'state.json'
state = {'options': {'core_engine': 'mediamtx_janus', 'mediamtx_rtsp_port': 9556,
                     'mediamtx_api_port': 10997, 'janus_enabled': True,
                     'hikvision_1_password': 'retired-fixture-password',
                     'detection_fps': 2, 'cameras': [], 'retention_days': 14},
         'network': {'8088/tcp': 8088, '8556/tcp': None, '9997/tcp': None,
                     '8443/tcp': None, '8889/tcp': 8889}, 'writes': 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def answer(self, status, data):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path != '/addons/self/info':
            self.answer(404, {})
            return
        self.answer(200, {'result': 'ok', 'data': {**state, 'slug': 'fixture_edge'}})

    def do_POST(self):
        if self.path == '/addons/self/uninstall':
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['uninstall_request'] = body
            state['uninstall_requests'] = state.get('uninstall_requests', 0) + 1
            state_path.write_text(json.dumps(state))
            self.answer(200, {'result': 'ok'})
            return
        if self.path == '/addons/self/restart':
            self.answer(200, {'result': 'ok'})
            return
        if self.path != '/addons/self/options':
            self.answer(404, {})
            return
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        state.update(options=body['options'], network=body.get('network', state['network']),
                     writes=state['writes'] + 1)
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state))
        os.replace(temporary, state_path)
        self.answer(200, {'result': 'ok'})


with socketserver.UnixStreamServer(str(root / 'api.sock'), Handler) as server:
    (root / 'ready').touch()
    server.serve_forever()
