"""Installation ownership, first-run setup and offline, explicit Edge data removal.

Supervisor removes /data when uninstalling the app; shared HA/media mounts survive.
Never infer an uninstall from a normal restart or silently erase a legacy upgrade.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
from urllib.parse import urlsplit

LOCK = threading.RLock()
CSRF = secrets.token_urlsafe(32)
OWNER = 'installation.json'
MARKER = 'edge-installation.json'
CONFIRM = 'DELETE EDGE'
MEDIA = Path('/media/edge-of-infinity/recordings')


def read(path, default=None):
    if path.is_symlink():
        raise ValueError('installation_symlink')
    if not path.exists():
        return default
    if path.stat().st_size > 131072:
        raise ValueError('installation_file_too_large')
    return json.loads(path.read_text())


def write(path, data):
    from edge_native_migration import atomic_json
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, data)
    os.chmod(path, 0o600)


def checked(path):
    """Reject symlink ancestors and broad/system roots before touching data."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or len(path.parts) < 3:
        raise ValueError('unsafe_cleanup_path')
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('cleanup_symlink_ancestor')
    if path.exists() and os.path.ismount(path):
        # Recording directories can themselves be mounted volumes: clear their
        # entries, never remove or traverse an unrelated nested mount.
        return path
    return path


def clear_directory(path):
    path = checked(path)
    if not path.exists():
        return
    for base, dirs, _ in os.walk(path, followlinks=False):
        for name in dirs:
            child = Path(base) / name
            if not child.is_symlink() and os.path.ismount(child):
                raise ValueError('cleanup_nested_mount')
    for entry in path.iterdir():
        if entry.is_symlink():
            entry.unlink()
        elif entry.is_dir():
            if os.path.ismount(entry):
                raise ValueError('cleanup_nested_mount')
            # fd-based rmtree does not follow substituted directory symlinks.
            shutil.rmtree(entry)
        else:
            entry.unlink()


def recording_roots(config):
    root = str((config.get('storage') or {}).get('recordings_dir') or MEDIA)
    cameras = config.get('cameras') or []
    names = {'.alerts'}
    for i, camera in enumerate(cameras):
        ident = re.sub(r'[^A-Za-z0-9_.-]', '_', str(camera.get('id') or f'camera_{i+1}'))
        if ident in ('.', '..', ''):
            continue
        names.update((ident, f'{ident}_{i}'))
    return [{'path': root, 'camera_dirs': sorted(names)}]


def merge_roots(*groups):
    result = {}
    for group in groups:
        for item in group:
            path = item['path']
            result.setdefault(path, set()).update(item.get('camera_dirs', []))
    return [{'path': path, 'camera_dirs': sorted(names)} for path, names in result.items()]


def remove_media(roots, media=MEDIA):
    for item in roots:
        root = checked(item['path'])
        if root.parts[1] in ('etc', 'usr', 'run', 'proc', 'sys', 'dev', 'var', 'root', 'homeassistant', 'config'):
            raise ValueError('unsafe_media_root')
        # All content below the dedicated Edge recordings root is Edge-owned.
        if root == media or media in root.parents:
            clear_directory(root)
        else:
            # A user-selected/shared directory may contain unrelated media.
            # Remove only registered Edge camera directories, never its root.
            for name in item.get('camera_dirs', []):
                if name in ('.', '..', '') or Path(name).name != name:
                    raise ValueError('unsafe_camera_directory')
                target = root / name
                if target.is_symlink():
                    target.unlink()
                elif target.exists():
                    clear_directory(target)
                    target.rmdir()


def blank_config(root):
    return {'server': {'listen': '0.0.0.0:8088', 'public_url': ''},
            'storage': {'recordings_dir': str(root), 'database_path': '/homeassistant/edge/edge.db',
                        'retention_days': 14}, 'live': {'engine': 'edge_rtc'}, 'cameras': []}


def bootstrap(home, private, addon, options, output, media=MEDIA):
    """Run before any recorder/engine starts. Return names/counters, no secrets."""
    home, private, addon = map(Path, (home, private, addon))
    checked(home); checked(addon / 'edge.json')
    private.mkdir(parents=True, exist_ok=True)
    owner = read(home / OWNER, {})
    marker = read(private / MARKER, {})
    for record in (owner, marker):
        if not isinstance(record, dict):
            raise ValueError('invalid_installation_marker')
        if record and not re.fullmatch(r'[0-9a-f]{32}', str(record.get('id', ''))):
            raise ValueError('invalid_installation_marker')
    config = read(home / 'edge.json', {})
    raw_options = read(Path(options), {})
    if not isinstance(raw_options, dict):
        raise ValueError('invalid_installation_options')
    legacy_options = bool(raw_options.get('cameras') or any(raw_options.get(key) for key in
        ('hikvision_1_host', 'hikvision_1_rtsp_main', 'hikvision_2_host', 'hikvision_2_rtsp_main')))
    if not owner and not (home / 'edge.json').exists():
        for source in (home / 'panel-config.json', addon / 'edge.json', home / 'edge.backup.json'):
            candidate = read(source, {})
            if isinstance(candidate, dict) and isinstance(candidate.get('cameras'), list):
                config = candidate
                write(home / 'edge.json', config)
                break
    if not isinstance(config, dict):
        raise ValueError('invalid_installation_config')
    if config and (not isinstance(config.get('cameras'), list)
                   or not all(isinstance(camera, dict) for camera in config['cameras'])
                   or not isinstance(config.get('storage', {}), dict)):
        raise ValueError('invalid_installation_config')
    pending = owner.get('pending_reset')
    if pending is not None and (not isinstance(pending, dict) or set(pending) != {'include_media'}
                                or type(pending.get('include_media')) is not bool):
        raise ValueError('invalid_reset_request')
    fresh = bool(owner and not marker)
    conflict = bool(owner and marker and marker['id'] != owner['id'])
    reset = fresh or bool(pending)
    roots = merge_roots(owner.get('recordings', []), owner.get('retained', []), recording_roots(config))
    mode = 'reinstalled' if fresh else 'reset' if reset else 'recovered' if conflict else 'upgrade' if config or legacy_options else 'new'
    if reset:
        if pending and pending.get('include_media') is True:
            roots = merge_roots(roots, [{'path': str(media), 'camera_dirs': []}])
            remove_media(roots, media)
            roots = []
        clear_directory(home)
        # Only known diagnostic/config mirrors; unrelated addon-config files stay.
        for name in ('edge.json', 'edge.example.json', 'edge.backup.json', 'panel-config.json'):
            path = addon / name
            if path.exists() or path.is_symlink():
                path.unlink()
        config = {}
    ident = secrets.token_hex(16) if reset else owner['id'] if owner else marker.get('id') or secrets.token_hex(16)
    # Private marker first: an interrupted first-upgrade adoption must not look
    # like an uninstall on the next boot and erase the old camera configuration.
    write(private / MARKER, {'id': ident})
    if not owner or reset:
        # A reinstall/reset gets its own recording namespace. Retained archives
        # cannot reappear just because a newly added camera uses the same ID.
        root = media / ident if reset or not (config or legacy_options) else Path((config.get('storage') or {}).get('recordings_dir') or media)
        owner = {'id': ident, 'setup_complete': False, 'legacy_upgrade': bool(config or (legacy_options and not reset)),
                 'generation_required': bool(reset or not (config or legacy_options)),
                 'recordings': recording_roots(config) if config else [{'path': str(root), 'camera_dirs': ['.alerts']}],
                 'retained': roots if reset else []}
        if not config and (reset or not legacy_options):
            config = blank_config(root)
            write(home / 'edge.json', config)
            write(addon / 'edge.json', config)
    elif not config:
        # Never re-import old mirrors/options after ownership has been established.
        root = media / owner['id']
        config = blank_config(root)
        write(home / 'edge.json', config)
        write(addon / 'edge.json', config)
    owner['recordings'] = merge_roots(owner.get('recordings', []), recording_roots(config))
    if conflict and not reset:
        # Partial backup restores can reintroduce another private marker.
        # Preserve the current configuration instead of inferring an uninstall.
        owner.update(setup_complete=False, legacy_upgrade=True)
    if mode == 'new' and media.exists() and any(p.name != owner['id'] for p in media.iterdir()):
        owner['retained'] = merge_roots(owner.get('retained', []), [{'path': str(media), 'camera_dirs': []}])
    write(home / OWNER, owner)
    if reset:
        from edge_native_migration import compact_options
        raw_options = compact_options(raw_options)
    write(Path(output), raw_options)
    report = {'event': 'installation_boot', 'phase': 'complete', 'mode': mode, 'configuration_reset': reset,
              'media_deleted': bool(pending and pending.get('include_media')), 'retained_locations': len(owner.get('retained', []))}
    write(home / 'installation-report.json', report)
    return report


def track_storage(home, config):
    with LOCK:
        path = Path(home) / OWNER
        data = read(path, {})
        if data:
            data['recordings'] = merge_roots(data.get('recordings', []), recording_roots(config))
            write(path, data)


def status(home):
    with LOCK:
        data = read(Path(home) / OWNER, {})
    return {'managed': bool(data), 'id': data.get('id'), 'needs_setup': bool(data and not data.get('setup_complete')),
            'mode': data.get('access_mode'), 'legacy_upgrade': bool(data.get('legacy_upgrade')),
            'retained_locations': len(data.get('retained', [])), 'pending_reset': bool(data.get('pending_reset')), 'csrf': CSRF}


def generation_matches(home, value):
    data = read(Path(home) / OWNER, {})
    return not data.get('pending_reset') and (not data.get('generation_required') or secrets.compare_digest(str(data.get('id', '')), str(value or '')))


def trusted(handler):
    try:
        peer = ipaddress.ip_address(handler.client_address[0])
    except ValueError:
        return False
    ingress = str(peer) == '172.30.32.2' and handler.headers.get('X-Ingress-Path', '').startswith('/api/hassio_ingress/')
    if not (peer.is_loopback or ingress):
        return False
    if handler.headers.get('Sec-Fetch-Site') == 'cross-site':
        return False
    origin = handler.headers.get('Origin')
    if origin and not ingress:
        allowed = (handler.headers.get('X-Forwarded-Host') or handler.headers.get('Host')) if ingress else handler.headers.get('Host')
        if urlsplit(origin).netloc != allowed:
            return False
    return True


def restart(log):
    # Give the HTTP response time to reach Ingress before Supervisor stops us.
    time.sleep(.5)
    try:
        from edge_supervisor import request_self
        request_self('restart', {})
    except (OSError, ValueError, RuntimeError):
        log('installation_restart_required', {'code': 'restart_app_from_home_assistant'})


def serve(handler, path, home, log):
    if path not in ('/api/setup', '/api/setup/access', '/api/setup/reset'):
        return False
    if not trusted(handler):
        handler.send_json({'error': 'open_home_assistant_ingress'}, 403)
        return True
    try:
        if handler.command == 'GET' and path == '/api/setup':
            handler.send_json(status(home))
            return True
        if handler.command != 'POST' or path == '/api/setup':
            handler.send_json({'error': 'method_not_allowed'}, 405)
            return True
        if handler.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('json_required')
        length_header = handler.headers.get('Content-Length')
        chunked = handler.headers.get('Transfer-Encoding', '').lower() == 'chunked'
        if (length_header is not None and not 0 < int(length_header) <= 4096) or (length_header is None and not chunked):
            raise ValueError('invalid_setup_body')
        payload = getattr(handler, 'read_setup_json', handler.read_body_json)()
        if not isinstance(payload, dict) or not secrets.compare_digest(str(payload.get('csrf', '')), CSRF):
            handler.send_json({'error': 'invalid_csrf'}, 403)
            return True
        with LOCK:
            state = read(Path(home) / OWNER, {})
            if not state:
                raise ValueError('installation_not_managed')
            if path.endswith('/access'):
                if payload.get('mode') not in ('local', 'nabu_casa'):
                    raise ValueError('invalid_access_mode')
                state.update(setup_complete=True, access_mode=payload['mode'])
                event = 'installation_setup_complete'
            else:
                if state.get('pending_reset'):
                    handler.send_json(status(home), 202)
                    return True
                if payload.get('confirm') != CONFIRM or type(payload.get('include_media')) is not bool:
                    raise ValueError('reset_confirmation_required')
                # Paths come exclusively from stored server configuration/ownership.
                config = read(Path(home) / 'edge.json', {})
                state['recordings'] = merge_roots(state.get('recordings', []), recording_roots(config))
                state['pending_reset'] = {'include_media': payload['include_media']}
                event = 'installation_reset_requested'
            write(Path(home) / OWNER, state)
        log(event, {'include_media': payload.get('include_media'), 'access_mode': payload.get('mode')})
        handler.send_json(status(home))
        if path.endswith('/reset'):
            threading.Thread(target=restart, args=(log,), daemon=True).start()
    except (OSError, ValueError, TypeError, KeyError):
        handler.send_json({'error': 'setup_request_failed'}, 400)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('home', 'private', 'addon', 'options', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    try:
        result = bootstrap(**vars(args))
        if result['configuration_reset']:
            for directory in ('/tmp/edge-runtime', '/tmp/edge-placeholder'):
                clear_directory(Path(directory))
        print('[edge-installation] ' + json.dumps(result), flush=True)
    except (OSError, ValueError, TypeError, KeyError) as error:
        safe_codes = {'installation_symlink', 'unsafe_cleanup_path', 'cleanup_symlink_ancestor',
                      'cleanup_nested_mount', 'unsafe_media_root', 'invalid_installation_marker',
                      'invalid_installation_config', 'invalid_installation_options', 'invalid_reset_request',
                      'unsafe_camera_directory'}
        code = str(error) if isinstance(error, ValueError) and str(error) in safe_codes else 'installation_boot_failed'
        print('[edge-installation] ERROR ' + json.dumps({'phase': 'before_camera_start', 'code': code,
              'errno': getattr(error, 'errno', None)}), flush=True)
        raise SystemExit(1)
