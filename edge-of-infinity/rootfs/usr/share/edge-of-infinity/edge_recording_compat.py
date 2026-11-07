"""Bounded phone playback remuxes, made exclusively from saved recordings."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from edge_recording_media import video_timing

MIB=1024*1024
MANAGED=re.compile(r'^[0-9a-f]{64}\.mp4$')


class CompatibleRecordings:
    def __init__(self, directory, budget=64*MIB, max_files=12, ttl=48*3600):
        self.directory=Path(directory);self.budget=budget;self.max_files=max_files;self.ttl=ttl
        self.lock=threading.Lock()

    def prune(self):
        if self.directory.is_symlink() or not self.directory.is_dir():return 0
        if not self.lock.acquire(timeout=.2):return 0
        try:return self._prune()[0]
        finally:self.lock.release()

    def _prune(self, reserve=0, slots=0):
        rows=[]
        for path in self.directory.iterdir():
            if MANAGED.fullmatch(path.name) and path.is_file() and not path.is_symlink():
                try:rows.append((path,path.stat()))
                except OSError:pass
        used=sum(stat.st_size for _,stat in rows);count=len(rows)
        for path,stat in sorted(rows,key=lambda row:row[1].st_mtime):
            if used+reserve<=self.budget and count+slots<=self.max_files and time.time()-stat.st_mtime<=self.ttl:continue
            try:path.unlink();used-=stat.st_size;count-=1
            except OSError:pass
        return used,count

    def prepare(self, source):
        source=Path(source)
        if source.is_symlink() or not source.is_file():raise RuntimeError('invalid_recording_source')
        stat=source.stat()
        # No full-day copies or transcodes. One original segment, copied once.
        reserve=int(stat.st_size*1.1)+MIB
        if reserve>self.budget:raise RuntimeError('recording_compat_budget')
        signature=f'{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:hvc1-v1'
        name=hashlib.sha256(signature.encode()).hexdigest()+'.mp4'
        if not self.lock.acquire(timeout=20):raise RuntimeError('recording_compat_busy')
        temporary=None
        try:
            if self.directory.is_symlink():raise RuntimeError('invalid_compat_directory')
            self.directory.mkdir(parents=True,exist_ok=True)
            target=self.directory/name
            if target.is_symlink():raise RuntimeError('invalid_compat_target')
            if target.is_file() and target.stat().st_size:
                os.utime(target,None);self._prune();return target
            used,count=self._prune(reserve,1)
            if used+reserve>self.budget or count>=self.max_files or shutil.disk_usage(self.directory).free<reserve+16*MIB:
                raise RuntimeError('recording_compat_disk_space')
            # This worker owns its only temporary file; crashes cannot leave a
            # growing collection. Foreign files/symlinks are never removed.
            temporary=self.directory/'remux.tmp'
            if temporary.is_symlink():raise RuntimeError('invalid_compat_temporary')
            command=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y','-threads','1',
                     '-fflags','+genpts','-i',str(source),'-map','0:v:0','-map','0:a?',
                     '-c','copy','-avoid_negative_ts','make_zero','-movflags','+faststart']
            if video_timing(source).get('codec_name')=='hevc':command+=['-tag:v','hvc1']
            command+=['-fs',str(reserve),'-f','mp4',str(temporary)]
            if shutil.which('nice'):command=['nice','-n','10',*command]
            result=subprocess.run(command,capture_output=True,timeout=20)
            if result.returncode or not temporary.is_file() or not 0<temporary.stat().st_size<reserve:
                raise RuntimeError('recording_compat_failed')
            current=source.stat()
            if (current.st_size,current.st_mtime_ns)!=(stat.st_size,stat.st_mtime_ns):raise RuntimeError('recording_source_changed')
            temporary.chmod(0o600);temporary.replace(target)
            return target
        finally:
            if temporary is not None and not temporary.is_symlink():temporary.unlink(missing_ok=True)
            self.lock.release()
