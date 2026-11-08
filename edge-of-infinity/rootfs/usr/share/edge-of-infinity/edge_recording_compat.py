"""Bounded phone playback remuxes, made exclusively from saved recordings."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import json

from edge_recording_media import video_timing

MIB=1024*1024
MANAGED=re.compile(r'^[0-9a-f]{64}\.(?:mp4|mov)$')


class CompatibleRecordings:
    def __init__(self, directory, budget=64*MIB, max_files=12, ttl=48*3600, log=lambda *args:None):
        self.directory=Path(directory);self.budget=budget;self.max_files=max_files;self.ttl=ttl
        self.lock=threading.Lock();self.log=log;self.recent={};self.stats_lock=threading.Lock()
        self.stats={'direct':0,'cached':0,'prepared':0,'tests':0}

    def report(self,event,payload):
        self.log(event,payload)

    def hit(self,kind,source,timing=None):
        now=time.monotonic();key=(kind,str(source))
        with self.stats_lock:
            self.stats[kind]+=1
            if now-self.recent.get(key,-60)<30:return
            self.recent[key]=now
            while len(self.recent)>32:self.recent.pop(next(iter(self.recent)))
        self.report('recording_compat_'+kind,{'filename':source.name,'media':timing or {},'video_transcoded':False})

    def status(self):
        with self.stats_lock:return {**self.stats,'worker_busy':self.lock.locked(),'budget_bytes':self.budget,'max_files':self.max_files}

    def prune(self):
        if self.directory.is_symlink() or not self.directory.is_dir():return 0
        if not self.lock.acquire(timeout=.2):return 0
        try:return self._prune()[0]
        finally:self.lock.release()

    def _prune(self, reserve=0, slots=0):
        abandoned=self.directory/'remux.tmp'
        if abandoned.is_file() and not abandoned.is_symlink():abandoned.unlink(missing_ok=True)
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

    def prepare(self, source, allow_passthrough=False):
        source=Path(source)
        if source.is_symlink() or not source.is_file():raise RuntimeError('invalid_recording_source')
        stat=source.stat()
        timing=video_timing(source)
        # New recordings already have fast-start indexes, zero-based video and
        # regular AAC. Serve those bytes directly; old/irregular files still remux.
        if allow_passthrough and timing.get('duration',0)>0 and timing.get('fps',0)>0 and timing.get('faststart') and timing.get('video_zero_start') and timing.get('audio_zero_start') and timing.get('sample_entry') in ('avc1','hvc1') and (not timing.get('audio_codec') or timing.get('audio_codec')=='mp4a' and timing.get('audio_clock_regular')):
            self.hit('direct',source,timing);return source
        # No full-day copies or transcodes. One original segment, copied once.
        reserve=int(stat.st_size*1.1)+MIB
        if reserve>self.budget:raise RuntimeError('recording_compat_budget')
        signature=f'{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:hvc1-clock-v4'
        name=hashlib.sha256(signature.encode()).hexdigest()+'.mp4'
        target=self.directory/name
        # Serving a cached Range/HEAD must not wait behind another remux.
        if not self.directory.is_symlink() and not target.is_symlink():
            try:
                if target.is_file() and target.stat().st_size:
                    os.utime(target,None);self.hit('cached',source);return target
            except FileNotFoundError:pass
        started=time.monotonic()
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
            queue_ms=round((time.monotonic()-started)*1000)
            command=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y','-threads','1','-filter_threads','1',
                     '-fflags','+genpts','-i',str(source),'-map','0:v:0','-map','0:a?',
                     '-c:v','copy',
                     '-bsf:v','setts=pts=PTS-STARTPTS:dts=DTS-STARTPTS',
                     '-avoid_negative_ts','disabled','-movflags','+faststart']
            if not timing.get('audio_codec') or timing.get('audio_codec')=='mp4a' and timing.get('audio_clock_regular'):
                command+=['-c:a','copy','-bsf:a','setts=pts=PTS-STARTPTS:dts=DTS-STARTPTS']
            else:
                command+=['-c:a','aac','-threads:a','1','-b:a','96k','-ar','48000','-ac','1',
                          '-af','asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0']
            if timing.get('codec_name')=='hevc':command+=['-tag:v','hvc1']
            command+=['-fs',str(reserve),'-f','mp4',str(temporary)]
            if shutil.which('nice'):command=['nice','-n','10',*command]
            self.report('recording_compat_start',{'filename':source.name,'queue_ms':queue_ms,'input':timing,'audio_mode':'copy' if command[command.index('-c:a')+1]=='copy' else 'aac_repair'})
            result=subprocess.run(command,capture_output=True,timeout=20)
            if result.returncode or not temporary.is_file() or not 0<temporary.stat().st_size<reserve:
                self.report('recording_compat_failed',{'filename':source.name,'returncode':result.returncode,'stderr':result.stderr.decode(errors='replace')[-1500:]})
                raise RuntimeError('recording_compat_failed')
            current=source.stat()
            if (current.st_size,current.st_mtime_ns)!=(stat.st_size,stat.st_mtime_ns):raise RuntimeError('recording_source_changed')
            temporary.chmod(0o600);temporary.replace(target)
            with self.stats_lock:self.stats['prepared']+=1
            self.report('recording_compat_done',{'filename':source.name,'elapsed_ms':round((time.monotonic()-started)*1000),'queue_ms':queue_ms,'size_bytes':target.stat().st_size,'output':video_timing(target),'video_transcoded':False})
            return target
        finally:
            if temporary is not None and not temporary.is_symlink():temporary.unlink(missing_ok=True)
            self.lock.release()

    def test_report(self,target,details,started,cached=False):
        transcode=details['video']!='source' and details['video']!=details['input'].get('codec_name')
        details={**details,'cached':cached,'elapsed_ms':round((time.monotonic()-started)*1000),'size_bytes':target.stat().st_size,
                 'output':video_timing(target),'video_transcoded':transcode,'lossless_video':not transcode}
        try:
            probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_name,codec_type,profile,pix_fmt,width,height,r_frame_rate,sample_rate,channels,bit_rate','-of','json',str(target)],capture_output=True,timeout=5)
            if probe.returncode==0:details['measured_streams']=json.loads(probe.stdout).get('streams',[])
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
        self.report('recording_codec_test_cached' if cached else 'recording_codec_test_done',details)
        return target,details

    def test_clip(self,source,video='source',audio='aac',start=0):
        """Explicit short codec experiment, sharing the normal single worker/budget."""
        if video not in ('source','h264','hevc') or audio not in ('aac','none','alaw','ulaw'):raise ValueError('invalid_test_codec')
        source=Path(source)
        if source.is_symlink() or not source.is_file():raise ValueError('invalid_recording_source')
        start=float(start)
        if not 0<=start<86400:raise ValueError('invalid_test_start')
        info=video_timing(source);start=round(min(start,max(0,info.get('duration',0)-.25)),2)
        if audio!='none' and not info.get('audio_codec'):raise ValueError('recording_has_no_audio_choose_none')
        stat=source.stat();suffix='.mov' if audio in ('alaw','ulaw') else '.mp4'
        ident=hashlib.sha256(f'{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{video}:{audio}:{start}:codec-test-v1'.encode()).hexdigest()+suffix
        if not self.lock.acquire(blocking=False):raise RuntimeError('recording_test_busy')
        temporary=None;started=time.monotonic()
        details={'filename':source.name,'video':video,'audio':audio,'start_seconds':start,'max_duration_seconds':6,'input':info,'original_unchanged':True}
        try:
            if self.directory.is_symlink():raise RuntimeError('invalid_compat_directory')
            self.directory.mkdir(parents=True,exist_ok=True);target=self.directory/ident
            if target.is_symlink():raise RuntimeError('invalid_compat_target')
            if target.is_file() and target.stat().st_size:
                os.utime(target,None);return self.test_report(target,details,started,cached=True)
            reserve=min(24*MIB,self.budget)
            used,count=self._prune(reserve,1)
            if reserve<MIB or used+reserve>self.budget or count>=self.max_files or shutil.disk_usage(self.directory).free<reserve+16*MIB:raise RuntimeError('recording_compat_disk_space')
            temporary=self.directory/'remux.tmp'
            if temporary.is_symlink():raise RuntimeError('invalid_compat_temporary')
            command=['ffmpeg','-hide_banner','-v','error','-nostdin','-y','-threads','1','-filter_threads','1','-ss',str(start),'-i',str(source),'-t','6','-map','0:v:0']
            if audio=='none':command+=['-an']
            else:command+=['-map','0:a?']
            codec=info.get('codec_name')
            transcode=video!='source' and video!=codec
            if transcode:
                command+=['-c:v','libx264' if video=='h264' else 'libx265','-threads:v','1','-preset','ultrafast','-crf','18','-g',str(max(1,round(info.get('fps') or 25)))]
                if video=='hevc':command+=['-x265-params','pools=none:frame-threads=1:log-level=error']
            else:command+=['-c:v','copy']
            if (video=='hevc' or video=='source' and codec=='hevc'):command+=['-tag:v','hvc1']
            if audio!='none':
                command+=['-c:a',{'aac':'aac','alaw':'pcm_alaw','ulaw':'pcm_mulaw'}[audio],'-threads:a','1','-ar','48000' if audio=='aac' else '8000','-ac','1','-af','asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0']
                if audio=='aac':command+=['-b:a','96k']
            command+=['-avoid_negative_ts','make_zero','-movflags','+faststart','-fs',str(reserve),'-f','mov' if suffix=='.mov' else 'mp4',str(temporary)]
            if shutil.which('nice'):command=['nice','-n','19',*command]
            self.report('recording_codec_test_start',details)
            result=subprocess.run(command,capture_output=True,timeout=90)
            if result.returncode or not temporary.is_file() or not 0<temporary.stat().st_size<reserve-65536:
                self.report('recording_codec_test_failed',{**details,'returncode':result.returncode,'stderr':result.stderr.decode(errors='replace')[-1500:]})
                raise RuntimeError('recording_codec_test_failed')
            current=source.stat()
            if (current.st_size,current.st_mtime_ns)!=(stat.st_size,stat.st_mtime_ns):raise RuntimeError('recording_source_changed')
            temporary.chmod(0o600);temporary.replace(target)
            with self.stats_lock:self.stats['tests']+=1
            return self.test_report(target,details,started)
        except Exception as error:
            self.report('recording_codec_test_error',{**details,'error':type(error).__name__+': '+str(error)})
            raise
        finally:
            if temporary is not None and not temporary.is_symlink():temporary.unlink(missing_ok=True)
            self.lock.release()
