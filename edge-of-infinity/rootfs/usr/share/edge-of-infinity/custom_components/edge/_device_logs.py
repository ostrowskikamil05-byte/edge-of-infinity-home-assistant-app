"""Bounded, cross-process device diagnostics shared by HA and the panel."""
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, urlunsplit

IDENT=re.compile(r'^[0-9a-f]{32}$')


def clean(value,depth=0):
    if depth>8:return '[depth limit]'
    if isinstance(value,dict):
        return {(safe_url(str(k)) if '://' in str(k) else str(k)[:80]):('***' if any(s in str(k).lower() for s in ('password','token','authorization','secret','authsig')) else clean(v,depth+1)) for k,v in list(value.items())[:64]}
    if isinstance(value,list):return [clean(v,depth+1) for v in value[:64]]
    if isinstance(value,str):
        value=value[:2000]
        value=re.sub(r'(?:https?|wss?|rtsp)://[^\s"<>]+',lambda m:safe_url(m[0]),value)
        return re.sub(r'(?i)((?:authsig|access_token|password|token|secret)=)[^&\s]+',r'\1***',value)
    return value if value is None or isinstance(value,(int,float,bool)) else str(value)[:160]


def safe_url(value):
    try:
        u=urlsplit(value);host=u.hostname or '';port=f':{u.port}' if u.port else ''
        if ':' in host:host='['+host+']'
        path=re.sub(r'/api/(?:hassio_ingress|hassio/ingress)/[^/]+','/ingress',u.path)
        return urlunsplit((u.scheme,host+port,path,'',''))
    except ValueError:return '[invalid URL]'


class DeviceLogs:
    max_devices=24
    max_bytes=256*1024
    ttl=7*86400

    def __init__(self,directory):self.directory=Path(directory)

    @contextmanager
    def locked(self):
        if self.directory.is_symlink():raise ValueError('invalid_device_log_directory')
        self.directory.mkdir(parents=True,exist_ok=True)
        lock=self.directory/'.lock'
        if lock.is_symlink():raise ValueError('invalid_device_log_lock')
        with lock.open('a') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(handle,fcntl.LOCK_UN)

    def files(self):
        return [p for p in self.directory.glob('*.jsonl') if IDENT.fullmatch(p.stem) and p.is_file() and not p.is_symlink()]

    def prune(self):
        rows=sorted(self.files(),key=lambda p:p.stat().st_mtime,reverse=True)
        for index,p in enumerate(rows):
            if index>=self.max_devices or time.time()-p.stat().st_mtime>self.ttl:p.unlink(missing_ok=True)

    def tail(self,path,limit=None):
        with path.open('rb') as handle:
            size=path.stat().st_size;start=max(0,size-(limit or self.max_bytes))
            handle.seek(start)
            if start:handle.readline()
            return handle.read(limit or self.max_bytes).decode('utf-8',errors='replace')

    def append(self,payload,peer=None):
        if not isinstance(payload,dict) or not isinstance(payload.get('device_id'),str) or not isinstance(payload.get('session_id'),str) or not IDENT.fullmatch(payload['device_id']) or not IDENT.fullmatch(payload['session_id']):raise ValueError('invalid_device_identity')
        if not isinstance(payload.get('device'),dict) or not isinstance(payload['device'].get('label',''),str):raise ValueError('invalid_device_description')
        raw=json.dumps(payload,ensure_ascii=False,allow_nan=False)
        if len(raw.encode())>32768:raise ValueError('device_report_too_large')
        data=clean(payload);data['received_at']=time.time();data['transport_observation']=clean(peer or {})
        line=json.dumps(data,ensure_ascii=False,allow_nan=False)+'\n'
        with self.locked():
            self.prune();path=self.directory/(payload['device_id']+'.jsonl')
            if path.is_symlink():raise ValueError('invalid_device_log')
            if path.exists() and time.time()-path.stat().st_mtime<.25:return False
            if path.exists() and path.stat().st_size+len(line.encode())>self.max_bytes:
                tail=self.tail(path,self.max_bytes//2)
                path.write_text(tail,encoding='utf-8')
            with path.open('a',encoding='utf-8') as handle:handle.write(line)
            path.chmod(0o600);self.prune()
        return True

    def snapshot(self,device_id=''):
        if device_id and not IDENT.fullmatch(device_id):raise ValueError('invalid_device_identity')
        with self.locked():
            self.prune();devices=[];selected=None
            for path in self.files():
                try:
                    data=json.loads(self.tail(path,65536).strip().splitlines()[-1])
                    devices.append({'device_id':path.stem,'label':data.get('device',{}).get('label') or path.stem[:8],
                                    'last_seen':data.get('received_at'),'session_id':data.get('session_id'),
                                    'user_agent':data.get('device',{}).get('user_agent')})
                    if path.stem==device_id:selected={'latest':data,'log':self.tail(path)}
                except (OSError,ValueError,IndexError,AttributeError):continue
            devices.sort(key=lambda d:d['last_seen'] or 0,reverse=True)
            return {'devices':devices,'selected':selected,'limits':{'devices':self.max_devices,'bytes_per_device':self.max_bytes,'retention_days':7},
                    'timing_note':'Zero DNS/TCP can mean connection reuse; unavailable phases are null. Client clock may differ; durations use its monotonic clock.',
                    'scope':'Browser-visible measurements and observed proxy addresses. No traceroute, router inspection or unavailable phone CPU/RAM measurements.'}
