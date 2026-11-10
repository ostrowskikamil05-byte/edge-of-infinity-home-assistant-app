"""One-time retirement of old engines; camera profiles and recordings stay intact."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.error
import urllib.request


RETIRED_PREFIXES = ('mediamtx_', 'janus_')
NATIVE_PORTS = {'8088/tcp', '8556/tcp', '9997/tcp'}


def native_options(options):
    result = {key: value for key, value in options.items()
              if key != 'core_engine' and not key.startswith(RETIRED_PREFIXES)}
    for new, old, default in (('edge_rtc_rtsp_port', 'mediamtx_rtsp_port', 8556),
                              ('edge_rtc_api_port', 'mediamtx_api_port', 9997)):
        value = result.get(new, default)
        if old in options and (new not in result or value == default):
            value = options[old]
        try:
            value = int(value)
            if not 1 <= value <= 65535:
                raise ValueError('port')
        except (ValueError, TypeError):
            value = default
        result[new] = value
    return result


def atomic_json(path, payload, preserve_time=False):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('configuration_symlink')
    content = json.dumps(payload, ensure_ascii=False, indent=2) + '\n'
    if path.exists() and path.read_text() == content:
        return False
    previous = path.stat() if preserve_time and path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
            name = handle.name
            handle.write(content)
        if previous:
            os.utime(name, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        os.replace(name, path)
        return True
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


def migrate_saved_files(home, addon, runtime):
    home, addon, runtime = Path(home), Path(addon), Path(runtime)
    report = {'configs_updated': 0, 'retired_files_removed': 0, 'skipped': []}
    # Preserve each file's own cameras and timestamps: a stale mirror can never
    # become authoritative merely because migration touched its engine keys.
    for base, names in ((home, ('edge.json', 'panel-config.json', 'edge.backup.json', 'edge.example.json')),
                        (addon, ('edge.json', 'edge.example.json'))):
        for name in names:
            path = base / name
            if not path.exists():
                continue
            try:
                if path.is_symlink():
                    raise ValueError('symlink')
                payload = json.loads(path.read_text())
                if not isinstance(payload, dict) or not isinstance(payload.get('cameras'), list):
                    continue
                clean = {key: value for key, value in payload.items()
                         if key != 'core_engine' and not key.startswith(RETIRED_PREFIXES)}
                clean['live'] = {'engine': 'edge_rtc'}
                if clean != payload and atomic_json(path, clean, preserve_time=True):
                    report['configs_updated'] += 1
            except (OSError, ValueError, TypeError):
                report['skipped'].append(name)
    # Only exact disposable engine artifacts; never traverse media, recordings,
    # database, model, alert, private-token or user-selected storage paths.
    for path in (runtime / 'mediamtx.yml', runtime / 'janus', home / 'mediamtx.yml',
                 home / 'janus.log', home / 'janus.plugin.streaming.jcfg'):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
                report['retired_files_removed'] += 1
            elif path == runtime / 'janus' and path.is_dir():
                shutil.rmtree(path)
                report['retired_files_removed'] += 1
        except OSError:
            report['skipped'].append(path.name)
    return report


def sync_supervisor_options(options, token, request=urllib.request.urlopen, output_path=None):
    if not token:
        return {'status': 'unavailable'}
    def call(path, payload=None):
        req = urllib.request.Request('http://supervisor/addons/self/' + path,
                                     data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
                                     method='POST' if payload is not None else 'GET')
        with request(req, timeout=5) as response:
            body = json.loads(response.read())
        if body.get('result') != 'ok':
            raise ValueError('supervisor_rejected')
        return body.get('data') or {}
    try:
        current = call('info')
        # Use Supervisor's current options when available, preserving unrelated
        # settings changed after this container's options file was mounted.
        existing = current.get('options')
        clean = native_options(existing) if isinstance(existing, dict) else options
        if output_path is not None:
            # Supervisor strips retired keys from the mounted options file,
            # while info still has their persisted values. Recover custom
            # listener ports before startup. Keep mounted/resolved credentials:
            # the API can contain raw !secret references intended for storage.
            runtime_options = dict(options)
            for key in ('edge_rtc_rtsp_port', 'edge_rtc_api_port'):
                runtime_options[key] = clean[key]
            atomic_json(output_path, runtime_options)
        payload = {'options': clean}
        network = current.get('network')
        if isinstance(network, dict):
            payload['network'] = {key: value for key, value in network.items() if key in NATIVE_PORTS}
        if clean != existing or isinstance(network, dict) and payload['network'] != network:
            call('options', payload)
        observed = call('info')
        return {'status': 'synced' if observed.get('options') == clean else 'verification_failed'}
    except (OSError, ValueError, TypeError):
        # Never serialize exceptions, request bodies, URLs or credentials.
        return {'status': 'retry_required'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--options', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--home', required=True)
    parser.add_argument('--addon', required=True)
    args = parser.parse_args()
    path = Path(args.options)
    options = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(options, dict):
        raise ValueError('invalid_app_options')
    clean = native_options(options)
    atomic_json(args.output, clean)
    report = migrate_saved_files(args.home, args.addon, Path(args.output).parent)
    report['options_removed'] = len(set(options) - set(clean))
    report['supervisor'] = sync_supervisor_options(clean, os.environ.get('SUPERVISOR_TOKEN'), output_path=args.output)
    atomic_json(Path(args.home) / 'native-migration.json', report)
    print('[edge-migration] ' + json.dumps(report))


if __name__ == '__main__':
    main()
