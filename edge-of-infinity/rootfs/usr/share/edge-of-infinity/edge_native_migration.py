"""One-time retirement of old engines; camera profiles and recordings stay intact."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import threading
import urllib.request

from edge_supervisor import SupervisorError, request_self, supervisor_available, supervisor_transport


RETIRED_PREFIXES = ('mediamtx_', 'janus_')
PANEL_OPTION_KEYS = {'cameras', 'sync_addon_options', 'camera_events_enabled',
                     'local_detection_enabled'}
NATIVE_PORTS = {'8088/tcp', '8556/tcp', '9997/tcp', '8443/tcp'}
JANUS_FILES = ('janus.jcfg', 'janus.transport.http.jcfg',
               'janus.transport.websockets.jcfg', 'janus.plugin.recordplay.jcfg',
               'janus.plugin.streaming.jcfg')


def panel_option(key):
    return key in PANEL_OPTION_KEYS or key.startswith(('hikvision_1_', 'hikvision_2_', 'detection_'))


def compact_options(options):
    """The panel owns camera/analysis settings after their successful import."""
    result = {key: value for key, value in native_options(options).items()
              if not panel_option(key)}
    # Supervisor requires the legacy object-list schema key; keep an empty
    # importer, never a second copy of camera credentials.
    result['cameras'] = []
    return result


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
                        (addon, ('edge.json', 'edge.example.json')),
                        (runtime, ('edge.options.json',))):
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
    def remove_file(path):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
                report['retired_files_removed'] += 1
        except OSError:
            report['skipped'].append(path.name)

    for base in (home, addon, runtime):
        for name in ('mediamtx.yml', 'mediamtx.yaml', 'mediamtx.log', 'janus.log', *JANUS_FILES):
            remove_file(base / name)
        directory = base / 'janus'
        if directory.is_symlink():
            remove_file(directory)
        elif directory.is_dir():
            # Never recursively erase an old directory: it may also hold user files.
            for name in JANUS_FILES:
                remove_file(directory / name)
            try:
                directory.rmdir()
                report['retired_files_removed'] += 1
            except OSError:
                report['skipped'].append('janus')
    return report


def sync_supervisor_options(options, token=None, request=None, output_path=None, *, compact=False):
    if not token and not supervisor_available():
        return {'status': 'unavailable'}
    def call(path, payload=None):
        if request is None:
            return request_self(path, payload, token=token)
        req = urllib.request.Request('http://supervisor/addons/self/' + path,
                                     data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
                                     method='POST' if payload is not None else 'GET')
        with request(req, timeout=5) as response:
            body = json.loads(response.read())
        if not isinstance(body, dict) or body.get('result') != 'ok':
            raise ValueError('supervisor_rejected')
        data = body.get('data') or {}
        if not isinstance(data, dict):
            raise ValueError('supervisor_response_invalid')
        return data
    phase = 'read_options'
    try:
        current = call('info')
        # Use Supervisor's current options when available, preserving unrelated
        # settings changed after this container's options file was mounted.
        existing = current.get('options')
        if not isinstance(existing, dict):
            raise ValueError('supervisor_options_missing')
        clean = compact_options(existing) if compact else native_options(existing)
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
        # Write once even when info already hides retired fields/bindings.
        # The API replaces persisted overrides; editing /data/options.json does not.
        phase = 'write_options'
        call('options', payload)
        phase = 'verify_options'
        observed = call('info')
        verified = observed.get('options') == clean
        if isinstance(network, dict):
            verified = verified and observed.get('network') == payload['network']
        result = {'status': 'synced' if verified else 'verification_failed'}
        if compact:
            result['scope'] = 'panel_owned_settings'
        if not verified:
            # Names only: values may include passwords or secret references.
            result.update(phase='verify_options', code='supervisor_options_not_replaced',
                          remaining_retired_keys=sorted(key for key in (observed.get('options') or {})
                              if key == 'core_engine' or key.startswith(RETIRED_PREFIXES)))
        return result
    except SupervisorError as error:
        result = {'status': 'retry_required', 'phase': phase, 'code': error.code}
        if error.http_status is not None:
            result['http_status'] = error.http_status
        return result
    except (OSError, ValueError, TypeError):
        # Never serialize exceptions, request bodies, URLs or credentials.
        return {'status': 'retry_required', 'phase': phase, 'code': 'supervisor_request_failed'}


def retry_supervisor_cleanup(home, stop, lock, log, *, sync=None, delays=(5, 15, 30, 60, 120)):
    """Retry startup failures off the media thread, rereading current settings each time."""
    sync = sync or sync_supervisor_options
    path = Path(home) / 'native-migration.json'
    for attempt, delay in enumerate(delays, 1):
        if stop.wait(delay):
            return
        with lock:
            if stop.is_set():
                return
            try:
                report = json.loads(path.read_text())
                if not isinstance(report, dict):
                    report = {}
            except (OSError, ValueError):
                report = {}
            previous = report.get('supervisor')
            if isinstance(previous, dict) and previous.get('status') == 'synced':
                return
            # No mounted fallback, credentials or camera projection: use the
            # Supervisor's latest complete options under the settings-save lock.
            result = sync({})
            report.update(supervisor=result, supervisor_attempts=attempt,
                          supervisor_transport=supervisor_transport())
            atomic_json(path, report)
        log('native_supervisor_cleanup', {'attempt': attempt, **result})
        if result.get('status') == 'synced':
            return


def start_supervisor_cleanup(home, stop, lock, log):
    if not supervisor_available():
        return None
    # Compact only after the runner has imported legacy cameras and the panel
    # has successfully loaded the authoritative configuration. A failed boot
    # must never delete the last saved camera/password configuration.
    config_path = Path(home) / 'edge.json'
    try:
        config = json.loads(config_path.read_text())
        ready = isinstance(config, dict) and isinstance(config.get('cameras'), list)
    except (OSError, ValueError):
        ready = False
    sync = None
    if ready:
        path = Path(home) / 'native-migration.json'
        try:
            report = json.loads(path.read_text())
            if not isinstance(report, dict):
                report = {}
        except (OSError, ValueError):
            report = {}
        report['supervisor'] = {'status': 'pending', 'scope': 'panel_owned_settings'}
        atomic_json(path, report)
        sync = lambda options: sync_supervisor_options(options, compact=True)
    worker = threading.Thread(target=retry_supervisor_cleanup,
                              args=(home, stop, lock, log), kwargs={'sync': sync}, daemon=True,
                              name='edge-supervisor-cleanup')
    worker.start()
    return worker


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
    report['supervisor_transport'] = supervisor_transport()
    report['supervisor'] = sync_supervisor_options(clean, os.environ.get('SUPERVISOR_TOKEN'), output_path=args.output)
    atomic_json(Path(args.home) / 'native-migration.json', report)
    print('[edge-migration] ' + json.dumps(report))


if __name__ == '__main__':
    main()
