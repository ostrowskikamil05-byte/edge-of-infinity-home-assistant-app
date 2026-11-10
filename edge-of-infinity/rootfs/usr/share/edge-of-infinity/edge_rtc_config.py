"""Private EdgeRTC source registry: one upstream for all roles of each camera."""
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile


def private_token(home):
    path = Path(home) / 'edge-rtc-token'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        token = path.read_text().strip()
        if len(token) >= 32:
            return token
        raise ValueError('Invalid private EdgeRTC token')
    token = secrets.token_urlsafe(48)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(token)
    return token


def build_config(config, home, rtsp_port=8556, api_port=9997):
    sources = {}
    for index, camera in enumerate(config.get('cameras', [])):
        if not camera.get('enabled'):
            continue
        profile = camera.get('record_stream') if camera.get('record_stream') in ('main', 'sub') else 'main'
        url = camera.get('rtsp_' + profile) or camera.get('rtsp_main')
        if not url:
            continue
        camera_id = re.sub(r'[^A-Za-z0-9_.-]', '_', camera.get('id') or f'camera_{index+1}')
        # Duplicate configured cameras pointing at the exact same URL share the registry too.
        row = sources.setdefault(url, {'id': camera_id, 'url': url, 'paths': [], 'microphone': True})
        for path in (camera_id + '_main', camera_id + '_sub'):
            if path not in row['paths']:
                row['paths'].append(path)
    archives={re.sub(r'[^A-Za-z0-9_.-]','_',c.get('id') or f'camera_{i+1}')+'_'+str(i):re.sub(r'[^A-Za-z0-9_.-]','_',c.get('id') or f'camera_{i+1}') for i,c in enumerate(config.get('cameras',[]))}
    from edge_local_network import local_settings
    network=local_settings(home)
    local_https={'enabled':network.get('enabled') is True,'listen':':8443','directory':str(Path(home)/'local-https'),'upstream':'http://homeassistant:8123'}
    return {'local_https':local_https,'rtsp': ':' + str(rtsp_port), 'http': ':' + str(api_port), 'token': private_token(home), 'sources': list(sources.values()),
            'recordings': str((config.get('storage') or {}).get('recordings_dir') or '/media/edge-of-infinity/recordings'), 'archives': archives}


def write_config(config, home, output, rtsp_port=8556, api_port=9997):
    payload = build_config(config, home, rtsp_port, api_port)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, separators=(',', ':'))
    if path.exists() and path.read_text() == content:
        return
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        handle.write(content)
        name = handle.name
    os.replace(name, path)


if __name__ == '__main__':
    config_path, home, output, rtsp_port, api_port = sys.argv[1:]
    write_config(json.loads(Path(config_path).read_text()), home, output, int(rtsp_port), int(api_port))
