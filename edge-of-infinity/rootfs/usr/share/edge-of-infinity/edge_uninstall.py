"""Confirmed, category-scoped self-uninstall. Cleanup runs before media startup."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time

from edge_installation import (CSRF, LOCK, MEDIA, OWNER, checked, clear_directory,
                               archived_roots, merge_roots, read, recording_roots, trusted, write)
from edge_supervisor import SupervisorError, request_self

SCOPES = ('recordings', 'alerts', 'thumbnails', 'snapshots', 'cache', 'settings', 'backups', 'history', 'integration')
CONFIRM = 'UNINSTALL EDGE'
JOB = 'edge-uninstall.json'
SHARED_JOB = 'uninstall.json'
ACK = 'uninstall-integration.json'
VIDEO = {'.mp4', '.mkv', '.mov', '.ts', '.m4v'}
ACTIVE = {'awaiting_integration', 'queued', 'cleaning', 'removing_app', 'failed', 'supervisor_pending'}


def selected(value):
    if not isinstance(value, dict) or set(value) != set(SCOPES) or any(type(v) is not bool for v in value.values()):
        raise ValueError('invalid_uninstall_selection')
    return value


def retained_root(home):
    return checked(Path(home).with_name(Path(home).name + '-retained'))


def registry(home):
    """Read only local Edge entry IDs, never modify HA's .storage directly."""
    path = Path(home).parent / '.storage/core.config_entries'
    checked(path)
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        return {'known': False, 'entries': [], 'total': 0}
    data = json.loads(path.read_text())
    entries = data.get('data', {}).get('entries')
    if not isinstance(entries, list):
        raise ValueError('invalid_ha_registry')
    edge = [e for e in entries if isinstance(e, dict) and e.get('domain') == 'edge']
    local = [str(e['entry_id']) for e in edge if isinstance(e.get('data'), dict)
             and str(e['data'].get('url', '')).lower() in ('', 'local', 'file', 'filesystem')]
    return {'known': True, 'entries': local, 'total': len(edge)}


def fingerprint(home):
    path = Path(home) / 'edge.json'
    checked(path)
    return hashlib.sha256(path.read_bytes() if path.exists() else b'').hexdigest()


def public(job, home):
    return {'id': job.get('id'), 'phase': job.get('phase', 'idle'), 'scopes': job.get('scopes', {}),
            'slug': job.get('slug'), 'integration': job.get('integration', {}),
            'deleted': job.get('deleted', 0), 'retained': job.get('retained', 0),
            'code': job.get('code'), 'retained_path': str(retained_root(home) / job['id']) if job.get('id') else None,
            'csrf': CSRF}


def plan(home, private, scopes, supervisor=None):
    supervisor = supervisor or request_self
    scopes = selected(scopes)
    owner = read(Path(home) / OWNER, {})
    if not owner.get('id') or owner.get('pending_reset') or owner.get('pending_uninstall'):
        raise ValueError('installation_busy')
    previous = read(Path(private) / JOB, {})
    if previous.get('phase') in ACTIVE:
        raise ValueError('uninstall_busy')
    info = supervisor('info')
    slug = info.get('slug')
    if not isinstance(slug, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', slug):
        raise ValueError('supervisor_app_unknown')
    core = registry(home)
    config = read(Path(home) / 'edge.json', {})
    job = {'id': secrets.token_hex(16), 'nonce': secrets.token_hex(32), 'phase': 'planned',
           'created': time.time(), 'installation_id': owner['id'], 'scopes': scopes,
           'fingerprint': fingerprint(home), 'slug': slug, 'integration': core,
           'roots': merge_roots(owner.get('recordings', []), owner.get('retained', []), recording_roots(config),archived_roots(home)),
           'deleted': 0, 'retained': 0}
    write(Path(private) / JOB, job)
    return job


def commit(home, private, ident, bridge_ready):
    job = read(Path(private) / JOB, {})
    owner = read(Path(home) / OWNER, {})
    if (job.get('phase') != 'planned' or job.get('id') != ident or time.time() - job.get('created', 0) > 900
            or owner.get('id') != job.get('installation_id') or fingerprint(home) != job.get('fingerprint')):
        raise ValueError('uninstall_plan_expired')
    needs_bridge = job['scopes']['integration'] and (not job['integration']['known'] or job['integration']['entries'])
    if needs_bridge and bridge_ready is not True:
        raise ValueError('ha_integration_bridge_required')
    job['phase'] = 'awaiting_integration' if needs_bridge else 'queued'
    if job['scopes']['integration'] and not needs_bridge:
        job['integration_ack'] = {'remaining': job['integration']['total']}
    write(Path(private) / JOB, job)
    write(Path(home) / SHARED_JOB, job)
    owner['pending_uninstall'] = job['id']
    write(Path(home) / OWNER, owner)
    return job


def integration_done(home, private, ident):
    job = read(Path(private) / JOB, {})
    ack = read(Path(home) / ACK, {})
    if (job.get('phase') != 'awaiting_integration' or job.get('id') != ident
            or ack.get('id') != ident or not secrets.compare_digest(str(ack.get('nonce', '')), job['nonce'])
            or ack.get('ok') is not True or type(ack.get('remaining')) is not int):
        raise ValueError('integration_removal_unconfirmed')
    job.update(phase='queued', integration_ack={'remaining': ack['remaining']})
    write(Path(private) / JOB, job)
    write(Path(home) / SHARED_JOB, job)
    return job


def classify(relative):
    parts = Path(relative).parts
    name, top = Path(relative).name.lower(), parts[0].lower()
    if top in {OWNER, SHARED_JOB, ACK, 'edge-rtc-token', 'health', 'health.json', 'cameras.json',
               'restream.json', 'ha-entities.json', 'installation-report.json', 'native-migration.json'}:
        return 'internal'
    if top in ('recording-thumbs', 'thumbnails'):
        return 'thumbnails'
    if top in ('snapshots', 'alerts'):
        return 'snapshots'
    if top in ('recording-cache', 'recording-compat', 'stream-lists'):
        return 'cache'
    if 'backup' in name or top in ('backups', 'exports'):
        return 'backups'
    if name.endswith(('.log', '.jsonl', '.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite-wal', '.sqlite-shm')) or top in ('device-logs', 'logs'):
        return 'history'
    return 'settings'


def media_class(relative):
    path = Path(relative)
    if '.alerts' in path.parts:
        return 'alerts'
    if path.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp'):
        return 'snapshots'
    if path.suffix.lower() in VIDEO or path.suffix.lower() in ('.json', '.idx'):
        return 'recordings'
    return 'history'


def tree_files(root, app_config=False):
    root = Path(root)
    checked(root / 'edge.json') if app_config and root == Path('/config') else checked(root)
    if not root.exists():
        return []
    result = []
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            p = Path(base) / name
            if p.is_symlink():
                result.append(p); dirs.remove(name)
            elif os.path.ismount(p):
                raise ValueError('cleanup_nested_mount')
        result.extend(Path(base) / name for name in files)
    return result


def media_targets(job, media):
    roots = [checked(media)]
    for item in job['roots']:
        root = checked(item['path'])
        if root == media or media in root.parents:
            continue
        if root.parts[1] in ('etc', 'usr', 'run', 'proc', 'sys', 'dev', 'var', 'root', 'homeassistant', 'config'):
            raise ValueError('unsafe_media_root')
        for name in item.get('camera_dirs', []):
            if Path(name).name != name or name in ('.', '..', ''):
                raise ValueError('unsafe_camera_directory')
            roots.append(checked(root / name))
    # Preflight every root before deletion, including nested mount checks.
    result = {}
    for root in roots:
        if root.is_symlink():
            raise ValueError('cleanup_symlink_ancestor')
        for p in tree_files(root):
            relative = p.relative_to(root)
            if root.name == '.alerts': relative = Path('.alerts') / relative
            result[str(p)] = (p, media_class(relative))
    return list(result.values())


def clean(home, private, addon, job, media=MEDIA):
    """Only called at process boot before EdgeRTC/recorders are started."""
    home, private, addon, media = map(Path, (home, private, addon, media))
    scopes = selected(job['scopes'])
    archive = retained_root(home) / job['id']
    checked(archive)
    source_files = [(home, tree_files(home)), (addon, tree_files(addon, app_config=True))]
    prior = tree_files(retained_root(home))
    targets = media_targets(job, media) if any(scopes[key] for key in ('recordings','alerts','snapshots','history')) else []
    # Refuse integration-file deletion if Core did not confirm removal, or if
    # the component is still needed by another Edge installation/entry.
    component = home.parent / 'custom_components/edge'
    remove_component = scopes['integration'] and job.get('integration_ack', {}).get('remaining') == 0
    if remove_component:
        checked(component); tree_files(component)
    job.update(phase='cleaning', code=None)
    write(private / JOB, job)
    for p, category in targets:
        if scopes[category] and (p.exists() or p.is_symlink()):
            p.unlink(); job['deleted'] += 1
    # Old retained files keep their original scope, not a blanket backup label.
    for p in prior:
        rel = p.relative_to(retained_root(home)); category = rel.parts[1] if len(rel.parts)>1 else 'history'
        if p.name == 'manifest.json' and len(rel.parts)==2:
            # Keep ownership of previously retained media even when deleting
            # history. A later full removal still needs its custom paths.
            if not all(scopes[key] for key in SCOPES if key!='integration'): continue
            category = 'history'
        if category in scopes and scopes[category]:
            p.unlink(); job['deleted'] += 1
    for root, files in source_files:
        for p in files:
            rel = p.relative_to(root)
            category = classify(rel)
            if category == 'internal' or scopes[category] or p.is_symlink():
                p.unlink(missing_ok=True); job['deleted'] += 1
            else:
                target = archive / category / ('home' if root == home else 'addon') / rel
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                checked(target)
                if target.exists():
                    # Retry after interruption: already retained files must not
                    # be overwritten by a newer/different active file.
                    raise ValueError('retained_destination_exists')
                shutil.move(str(p), str(target)); os.chmod(target, 0o600)
                job['retained'] += 1
    if not scopes['settings']:
        source = private / 'options.json'
        if source.is_file():
            target = archive / 'settings/addon-options.json'
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not target.exists():
                shutil.copyfile(source, target); os.chmod(target, 0o600)
    for root, _ in source_files:
        # Every file was explicitly deleted or archived; remove empty folders
        # without trying to remove a /config bind mount itself.
        for base, dirs, _ in os.walk(root, topdown=False, followlinks=False):
            for name in dirs:
                child=Path(base)/name
                if not child.is_symlink():
                    try: child.rmdir()
                    except OSError: pass
    if remove_component and component.exists():
        clear_directory(component); component.rmdir()
    if archive.exists() or not all(scopes[key] for key in SCOPES if key!='integration'):
        write(archive / 'manifest.json', {'id': job['id'], 'roots': job['roots'], 'scopes': scopes})
    job.update(phase='removing_app',clean_completed=True)
    write(private / JOB, job)
    return job


def request_removal(private, job, supervisor=None):
    supervisor = supervisor or request_self
    try:
        supervisor('uninstall', {'remove_config': True})
        job.update(phase='supervisor_pending', code=None)
    except SupervisorError as error:
        # Supervisor shields uninstall from the caller disappearing when it
        # stops this very container. A disconnect alone proves no outcome.
        uncertain = error.code == 'supervisor_connection_failed'
        job.update(phase='supervisor_pending' if uncertain else 'failed', code=error.code)
    write(Path(private) / JOB, job)


def serve(handler, path, home, private=None, addon=None, on_cancel=None):
    paths = {'/api/uninstall', '/api/uninstall/plan', '/api/uninstall/commit', '/api/uninstall/run', '/api/uninstall/cancel'}
    if path not in paths: return False
    if not trusted(handler):
        handler.send_json({'error':'open_home_assistant_ingress'},403); return True
    private = Path(private or os.environ.get('EDGE_PRIVATE_DIR', '/data'))
    try:
        with LOCK:
            job = read(private / JOB, {})
            if handler.command == 'GET' and path == '/api/uninstall':
                state = public(job, home); state['registry'] = registry(home)
                handler.send_json(state); return True
            if handler.command != 'POST':
                raise ValueError('method_not_allowed')
            if handler.headers.get('Content-Type','').split(';')[0] != 'application/json':
                raise ValueError('json_required')
            length = handler.headers.get('Content-Length')
            if (length is not None and not 0<int(length)<=4096) or (length is None and handler.headers.get('Transfer-Encoding','').lower()!='chunked'):
                raise ValueError('invalid_body')
            payload = getattr(handler,'read_setup_json',handler.read_body_json)()
            if not isinstance(payload,dict) or not secrets.compare_digest(str(payload.get('csrf','')),CSRF):
                handler.send_json({'error':'invalid_csrf'},403); return True
            if path.endswith('/plan'):
                job = plan(home, private, payload.get('scopes'))
            elif path.endswith('/commit'):
                if payload.get('confirm') != CONFIRM: raise ValueError('uninstall_confirmation_required')
                job = commit(home, private, payload.get('id'), payload.get('bridge_ready'))
            elif path.endswith('/run'):
                job = integration_done(home,private,payload.get('id'))
            elif path.endswith('/cancel'):
                if job.get('id') != payload.get('id') or job.get('phase') not in ('planned','awaiting_integration'):
                    raise ValueError('uninstall_cannot_cancel')
                owner=read(Path(home)/OWNER,{})
                owner.pop('pending_uninstall',None);write(Path(home)/OWNER,owner)
                job['phase']='cancelled';write(private/JOB,job)
                if on_cancel:
                    threading.Thread(target=on_cancel,daemon=True).start()
            else: raise ValueError('invalid_uninstall_action')
        if job.get('phase')=='queued':
            from edge_installation import restart
            threading.Thread(target=restart,args=(lambda *args:None,),daemon=True).start()
        handler.send_json(public(job,home))
    except SupervisorError as error:
        handler.send_json({'error':error.code},503)
    except (OSError,ValueError,TypeError,KeyError):
        handler.send_json({'error':'uninstall_request_failed'},400)
    return True


def small_json(stream, headers):
    if headers.get('Transfer-Encoding','').lower()=='chunked':
        data=bytearray()
        while True:
            line=stream.readline(128)
            if not line.endswith(b'\r\n'):raise ValueError('invalid_chunk')
            length=int(line.split(b';',1)[0].strip(),16)
            if length<0 or len(data)+length>4096:raise ValueError('body_too_large')
            if not length:
                if stream.readline(128)!=b'\r\n':raise ValueError('invalid_trailer')
                break
            block=stream.read(length)
            if len(block)!=length or stream.read(2)!=b'\r\n':raise ValueError('invalid_chunk')
            data.extend(block)
        raw=bytes(data)
    else:
        length=int(headers.get('Content-Length',0))
        if not 0<length<=4096:raise ValueError('body_too_large')
        raw=stream.read(length)
        if len(raw)!=length:raise ValueError('short_body')
    payload=json.loads(raw)
    if not isinstance(payload,dict):raise ValueError('json_object_required')
    return payload


def run_pending(home, private, addon):
    """Return False for ordinary boots; keep maintenance mode alive on failure."""
    private=Path(private);job=read(private/JOB,{})
    if job.get('phase') not in {'queued','cleaning','removing_app','failed','supervisor_pending'}:
        return False
    marker=read(private/'edge-installation.json',{})
    if marker.get('id') != job.get('installation_id'):
        raise ValueError('uninstall_installation_changed')
    options=read(private/'options.json',{})
    port=int(options.get('api_port') or 8088)
    if not 1<=port<=65535: raise ValueError('invalid_api_port')
    wake=threading.Event()
    class Maintenance(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            from urllib.parse import urlsplit
            path='/'+self.path.split('?',1)[0].lstrip('/')
            kind='application/json'
            if path=='/health':
                body=b'{"status":"ok","mode":"uninstall"}'
            elif path=='/api/uninstall' and trusted(self):
                body=json.dumps(public(read(private/JOB,{}),home)).encode()
            elif path in ('/','/index.html'):
                kind='text/html; charset=utf-8'
                body=b'<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><body><script type="module">import {openUninstall} from "./api/edge-rtc/edge-uninstall.js";openUninstall({endpoint:"./api/uninstall"});</script></body>'
            elif path=='/api/edge-rtc/edge-uninstall.js':
                kind='text/javascript';body=(Path(__file__).parent/'custom_components/edge/www/edge-uninstall.js').read_bytes()
            else:
                self.send_response(503);self.end_headers();return
            self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):
            if '/'+self.path.split('?',1)[0].lstrip('/')!='/api/uninstall/retry' or not trusted(self):
                self.send_response(403);self.end_headers();return
            try:
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('json_required')
                payload=small_json(self.rfile,self.headers)
                current=read(private/JOB,{})
                if (not secrets.compare_digest(str(payload.get('csrf','')),CSRF) or payload.get('id')!=current.get('id')
                        or current.get('phase') not in ('failed','supervisor_pending')):raise ValueError('invalid_retry')
                current.update(phase='removing_app' if current.get('clean_completed') else 'queued',code=None)
                write(private/JOB,current);wake.set()
                body=json.dumps(public(current,home)).encode();self.send_response(200)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            except (OSError,ValueError,TypeError):
                self.send_response(400);self.end_headers()
    server=ThreadingHTTPServer(('0.0.0.0',port),Maintenance)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    while True:
        job=read(private/JOB,{})
        try:
            if job['phase'] in {'queued','cleaning'}:
                clean(home,private,addon,job)
            if job['phase']=='removing_app':
                request_removal(private,job)
        except (OSError,ValueError,KeyError,TypeError):
            job.update(phase='failed',code='uninstall_cleanup_failed');write(private/JOB,job)
        print('[edge-uninstall] '+json.dumps({k:job.get(k) for k in ('id','phase','deleted','retained','code')}),flush=True)
        # Stay idle until an explicit retry or Supervisor removes this container.
        wake.wait();wake.clear()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for key in ('home','private','addon'):parser.add_argument('--'+key,required=True)
    args=parser.parse_args();run_pending(**vars(args))
