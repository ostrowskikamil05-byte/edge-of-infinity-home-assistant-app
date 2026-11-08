"""Bounded shared archive snapshots. No media decoding or camera connections."""
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import threading
import time


class RecordingIndexCache:
    def __init__(self,build,log=lambda *a:None,max_files=24000):
        self.build,self.log,self.max_files=build,log,max_files
        self.guard=threading.Lock();self.worker=threading.Lock()
        self.entries=OrderedDict();self.latest=OrderedDict();self.primed=set()

    def key(self,config,selection):
        source={k:config.get(k) for k in ('cameras','storage','nvr')}
        signature=hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()
        with self.guard:latest=self.latest.get(signature,{})
        days=tuple(selection.get(str(i)) or selection.get(str(c.get('id') or '')) or latest.get(str(i),'') for i,c in enumerate(config.get('cameras',[])))
        return signature,days

    def lookup(self,key):
        with self.guard:
            cached=self.entries.get(key)
            if cached and time.monotonic()-cached[0]<300:
                self.entries.move_to_end(key)
                return cached
        return None

    def save(self,config,selection,data):
        signature,_=self.key(config,selection)
        days=tuple(c.get('selected_day','') for c in data['cameras'])
        size=sum(len(c.get('files',[])) for c in data['cameras'])
        if size>self.max_files:return
        stamp=self.directory_stamp(data)
        with self.guard:
            self.latest[signature]={str(c['index']):(c.get('days') or [{}])[0].get('day','') for c in data['cameras']}
            self.latest.move_to_end(signature)
            while len(self.latest)>8:self.latest.popitem(last=False)
            self.entries[(signature,days)]=(time.monotonic(),data,size,stamp)
            self.entries.move_to_end((signature,days))
            while len(self.entries)>4 or sum(row[2] for row in self.entries.values())>self.max_files:self.entries.popitem(last=False)

    @staticmethod
    def directory_stamp(data):
        result=[]
        for camera in data.get('cameras',[]):
            path=camera.get('directory')
            if not path:continue
            try:stat=Path(path).stat();result.append((path,stat.st_mtime_ns,stat.st_ctime_ns))
            except OSError:result.append((path,None,None))
        return tuple(result)

    def refresh(self,config,selection):
        started=time.monotonic();data=self.build(config,selection)
        self.save(config,selection,data)
        self.log('recording_catalog_built',{'elapsed_ms':round((time.monotonic()-started)*1000),'files':sum(len(c.get('files',[])) for c in data['cameras'])})
        return data

    def warm(self,config,selection):
        if not self.worker.acquire(blocking=False):return False
        def run():
            try:
                if hasattr(os,'setpriority') and hasattr(os,'PRIO_PROCESS'):
                    try:os.setpriority(os.PRIO_PROCESS,threading.get_native_id(),10)
                    except OSError:pass
                self.refresh(config,selection)
            except Exception as error:self.log('recording_catalog_error',{'error':type(error).__name__+': '+str(error)})
            finally:self.worker.release()
        threading.Thread(target=run,daemon=True).start();return True

    def warm_once(self,config):
        signature,_=self.key(config,{})
        with self.guard:
            if signature in self.primed:return
            if len(self.primed)>=8:self.primed.clear()
            self.primed.add(signature)
        if not self.warm(config,{}):
            with self.guard:self.primed.discard(signature)

    def get(self,config,selection,force=False):
        requested_at=time.monotonic()
        key=self.key(config,selection);cached=self.lookup(key)
        if cached and not force:
            age=time.monotonic()-cached[0];stale=age>=10 or self.directory_stamp(cached[1])!=cached[3]
            refreshing=self.warm(config,selection) if stale else False
            self.log('recording_catalog_hit',{'age_ms':round(age*1000),'stale':stale})
            return {**cached[1],'cameras':[dict(c) for c in cached[1]['cameras']], 'index_stale':stale,'index_refreshing':refreshing or self.worker.locked()}
        # A concurrent phone or the Live warmup builds the same catalog once.
        with self.worker:
            cached=self.lookup(self.key(config,selection))
            data=cached[1] if cached and (not force or cached[0]>=requested_at) else self.refresh(config,selection)
            return {**data,'cameras':[dict(c) for c in data['cameras']], 'index_stale':False}
