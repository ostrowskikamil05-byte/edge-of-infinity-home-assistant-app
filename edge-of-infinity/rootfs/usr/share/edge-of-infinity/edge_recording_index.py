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

    def key(self,config,selection,indices=None):
        source={k:config.get(k) for k in ('cameras','storage','nvr')}
        if indices is not None:source['indices']=list(indices)
        signature=hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()
        with self.guard:latest=self.latest.get(signature,{})
        days=tuple(selection.get(str(i)) or selection.get(str(c.get('id') or '')) or latest.get(str(i),'') for i,c in enumerate(config.get('cameras',[])) if indices is None or i in indices)
        return signature,days

    def lookup(self,key):
        with self.guard:
            cached=self.entries.get(key)
            if cached and time.monotonic()-cached[0]<300:
                self.entries.move_to_end(key)
                return cached
        return None

    def save(self,config,selection,data,indices=None):
        signature,_=self.key(config,selection,indices)
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

    def refresh(self,config,selection,indices=None):
        started=time.monotonic();data=self.build(config,selection) if indices is None else self.build(config,selection,indices)
        self.save(config,selection,data,indices)
        self.log('recording_catalog_built',{'elapsed_ms':round((time.monotonic()-started)*1000),'files':sum(len(c.get('files',[])) for c in data['cameras']),'camera_indices':list(indices) if indices is not None else 'all'})
        return data

    def warm(self,config,selection,indices=None):
        if not self.worker.acquire(blocking=False):return False
        def run():
            try:
                if hasattr(os,'setpriority') and hasattr(os,'PRIO_PROCESS'):
                    try:os.setpriority(os.PRIO_PROCESS,threading.get_native_id(),10)
                    except OSError:pass
                self.refresh(config,selection,indices)
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

    def scoped_snapshot(self,config,selection,indices):
        if indices is None:return None
        shared=self.lookup(self.key(config,selection))
        if not shared:return None
        data={**shared[1],'cameras':[c for c in shared[1]['cameras'] if c['index'] in indices]}
        paths={c.get('directory') for c in data['cameras']}
        return shared[0],data,sum(len(c.get('files',[])) for c in data['cameras']),tuple(stamp for stamp in shared[3] if stamp[0] in paths)

    def get(self,config,selection,force=False,indices=None):
        requested_at=time.monotonic()
        cached=self.lookup(self.key(config,selection,indices))
        if not cached and not force:cached=self.scoped_snapshot(config,selection,indices)
        if cached and not force:
            age=time.monotonic()-cached[0];stale=age>=10 or self.directory_stamp(cached[1])!=cached[3]
            refreshing=self.warm(config,selection,indices) if stale else False
            self.log('recording_catalog_hit',{'age_ms':round(age*1000),'stale':stale,'camera_indices':list(indices) if indices is not None else 'all'})
            return {**cached[1],'cameras':[dict(c) for c in cached[1]['cameras']], 'index_stale':stale,'index_refreshing':refreshing or self.worker.locked()}
        # A concurrent phone or Live warmup may just have built the full catalog.
        with self.worker:
            cached=self.lookup(self.key(config,selection,indices))
            if not cached and not force:cached=self.scoped_snapshot(config,selection,indices)
            data=cached[1] if cached and (not force or cached[0]>=requested_at) else self.refresh(config,selection,indices)
            return {**data,'cameras':[dict(c) for c in data['cameras']], 'index_stale':False}
