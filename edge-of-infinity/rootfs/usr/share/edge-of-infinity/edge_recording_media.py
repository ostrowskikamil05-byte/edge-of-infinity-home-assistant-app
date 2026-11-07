"""Recorded-video timing and bounded intrusion media, independent of recording."""
from pathlib import Path
import json
import os
import queue
import shutil
import subprocess
import threading
import time

MIB = 1024 * 1024
_TIMING_LOCK = threading.Lock()
_TIMING = {}


def video_timing(path):
    """Read the video track's sample clock without decoding or spawning FFprobe."""
    path = Path(path)
    stat = path.stat()
    signature = (stat.st_size, stat.st_mtime_ns)
    with _TIMING_LOCK:
        cached = _TIMING.get(str(path))
    if cached and cached[0] == signature:
        return cached[1]
    result = {'fps': 0.0, 'duration': 0.0, 'frame_seconds': 0.0}
    with path.open('rb') as handle:
        def boxes(start,end):
            cursor=start
            while cursor+8<=end:
                handle.seek(cursor); header=handle.read(8)
                if len(header)!=8:return
                size,kind=int.from_bytes(header[:4],'big'),header[4:8]
                extra=8
                if size==1:
                    raw=handle.read(8)
                    if len(raw)!=8:return
                    size=int.from_bytes(raw,'big');extra=16
                elif size==0:size=end-cursor
                if size<extra or cursor+size>end:return
                yield kind,cursor+extra,cursor+size
                cursor+=size
        for kind,start,end in boxes(0,stat.st_size):
            if kind!=b'moov':continue
            for child,track_start,track_end in boxes(start,end):
                if child!=b'trak':continue
                for child,media_start,media_end in boxes(track_start,track_end):
                    if child!=b'mdia':continue
                    timescale=duration=0;video=False;table=None
                    for child,a,b in boxes(media_start,media_end):
                        if child==b'hdlr':
                            handle.seek(a);video=handle.read(12)[8:12]==b'vide'
                        elif child==b'mdhd':
                            handle.seek(a);data=handle.read(min(32,b-a))
                            offset=20 if data and data[0]==1 else 12
                            if len(data)>=offset+8:
                                timescale=int.from_bytes(data[offset:offset+4],'big')
                                duration=int.from_bytes(data[offset+4:offset+(12 if offset==20 else 8)],'big')
                        elif child==b'minf':
                            for child,c,d in boxes(a,b):
                                if child!=b'stbl':continue
                                for child,e,f in boxes(c,d):
                                    if child==b'stts':table=(e,f)
                    if not video or not timescale:continue
                    count=ticks=0
                    if table:
                        handle.seek(table[0]);head=handle.read(8)
                        entries=int.from_bytes(head[4:8],'big') if len(head)==8 else 0
                        if entries<=100000 and entries*8<=table[1]-table[0]-8:
                            for _ in range(entries):
                                raw=handle.read(8);n=int.from_bytes(raw[:4],'big');delta=int.from_bytes(raw[4:],'big')
                                count+=n;ticks+=n*delta
                    fps=timescale*count/ticks if ticks else 0
                    result={'fps':round(fps,6) if .1<=fps<=240 else 0.0,'duration':duration/timescale if duration else 0.0,
                            'frame_seconds':1/fps if .1<=fps<=240 else 0.0}
    with _TIMING_LOCK:
        if len(_TIMING)>=512:_TIMING.pop(next(iter(_TIMING)))
        _TIMING[str(path)]=(signature,result)
    return result


class AlertMediaService:
    """One low-priority remux worker; bounded queue and clips; originals untouched."""
    def __init__(self, store, config, entries, save_image, fallback_image, log=lambda *a:None, start=True):
        self.store,self.config,self.entries=store,config,entries
        self.save_image,self.fallback_image,self.log=save_image,fallback_image,log
        self.queue=queue.Queue(maxsize=8);self.pending={};self.stop=threading.Event()
        self.thread=threading.Thread(target=self._run,daemon=True) if start else None
        if self.thread:self.thread.start()

    def enqueue(self,event,frame=None):
        self.store.update_media(event['id'],clip_state='pending')
        try:self.queue.put_nowait((dict(event),frame))
        except queue.Full:self.store.update_media(event['id'],clip_state='unavailable',media_error='alert_queue_full')

    @staticmethod
    def directory(config):
        return Path((config.get('storage') or {}).get('recordings_dir') or '/media/edge-of-infinity/recordings')/'.alerts'

    def path(self, ident):
        if not 0<int(ident)<=9223372036854775807:raise ValueError('invalid_alert_id')
        return self.directory(self.config())/f'event-{int(ident)}.mp4'

    def prune(self,reserve=0):
        config=self.config();options=config.get('detection') or {}
        budget=int(options.get('alert_media_mb',256))*MIB
        root=self.directory(config)
        photos=self.store.path.parent/'alerts'
        if photos.is_dir() and not photos.is_symlink():
            images=[]
            for path in photos.glob('event-*.jpg'):
                if path.is_symlink() or not path.stem[6:].isdigit():continue
                try:images.append((path,path.stat()))
                except OSError:pass
            used_images=sum(stat.st_size for _,stat in images)
            image_count=len(images)
            days=int((config.get('nvr') or {}).get('retention_days',14))
            for path,stat in sorted(images,key=lambda item:item[1].st_mtime):
                if time.time()-stat.st_mtime<=days*86400 and used_images<=32*MIB and image_count<=2000:continue
                try:
                    path.unlink();path.with_suffix('.json').unlink(missing_ok=True);used_images-=stat.st_size;image_count-=1
                    self.store.update_media(int(path.stem[6:]),has_image=False)
                except OSError:pass
        if not root.is_dir() or root.is_symlink():return {'bytes':0,'limit_bytes':budget}
        for pattern in ('event-*.tmp.mp4','event-*.ffconcat'):
            for path in root.glob(pattern):
                ident=path.name.split('.',1)[0][6:]
                if ident.isdigit() and not path.is_symlink():path.unlink(missing_ok=True)
        rows=[]
        for path in root.glob('event-*.mp4'):
            if path.is_symlink() or not path.stem[6:].isdigit():continue
            try:rows.append((path,path.stat()))
            except OSError:pass
        used=sum(stat.st_size for _,stat in rows)
        retention=int((config.get('nvr') or {}).get('retention_days',14))*86400
        for path,stat in sorted(rows,key=lambda item:item[1].st_mtime):
            if time.time()-stat.st_mtime<=retention and used+reserve<=budget:continue
            try:path.unlink();used-=stat.st_size;self.store.update_media(int(path.stem[6:]),clip_state='expired')
            except OSError:pass
        return {'bytes':used,'limit_bytes':budget}

    def build(self,event):
        if self.store.get(event['id']) is None:return
        config=self.config();options=config.get('detection') or {}
        before=int(options.get('pre_seconds',5));after=int(options.get('post_seconds',15))
        rows=self.entries(event['camera_id'],event['timestamp']-before,event['timestamp']+after)
        if not rows or not any(row['start_ts']-.2<=event['timestamp']<row['start_ts']+row['duration'] for row in rows):
            self.store.update_media(event['id'],clip_state='unavailable',media_error='recording_gap_or_disabled');return
        estimate=int(sum(row['path'].stat().st_size for row in rows)*1.1)+MIB
        budget=int(options.get('alert_media_mb',256))*MIB
        if estimate>budget:
            self.store.update_media(event['id'],clip_state='unavailable',media_error='alert_larger_than_budget');return
        inventory=self.prune(estimate)
        root=self.directory(config);root.mkdir(parents=True,exist_ok=True)
        if root.is_symlink():raise RuntimeError('invalid_alert_directory')
        if inventory['bytes']+estimate>budget or shutil.disk_usage(root).free<estimate+16*MIB:
            self.store.update_media(event['id'],clip_state='unavailable',media_error='alert_disk_space');return
        target=self.path(event['id']);tmp=target.with_suffix('.tmp.mp4');concat=target.with_suffix('.ffconcat')
        command=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y','-f','concat','-safe','0','-i',str(concat),
                 '-map','0:v:0','-map','0:a?','-c','copy','-avoid_negative_ts','make_zero','-movflags','+faststart',str(tmp)]
        if shutil.which('nice'):command=['nice','-n','10',*command]
        try:
            concat.write_text('\n'.join("file '"+str(row['path']).replace("'","'\\''")+"'" for row in rows)+'\n')
            result=subprocess.run(command,capture_output=True,timeout=90)
            if result.returncode or not tmp.exists() or tmp.stat().st_size>estimate:raise RuntimeError('alert_remux_failed')
            offset=0.0
            for row in rows:
                if row['start_ts']+row['duration']<=event['timestamp']:offset+=row['duration']
                else:
                    offset+=max(0,event['timestamp']-row['start_ts']);break
            tmp.replace(target);os.utime(target,(time.time(),event['timestamp']))
            self.store.update_media(event['id'],clip_state='ready',clip_offset_seconds=round(offset,3),
                                    clip_start_ts=rows[0]['start_ts'],clip_seconds=round(sum(row['duration'] for row in rows),3),clip_bytes=target.stat().st_size,media_error=None)
            current=self.store.get(event['id'])
            if current and not current.get('has_image'):
                self.fallback_image(event,target,offset)
        finally:
            tmp.unlink(missing_ok=True);concat.unlink(missing_ok=True)

    def _run(self):
        for event in self.store.recent(limit=200):
            if event.get('clip_state')=='pending':self.pending[event['id']]=(event,time.time())
        last_prune=0
        while not self.stop.is_set():
            try:
                event,frame=self.queue.get(timeout=.2)
                if frame is not None:
                    try:self.save_image(event,frame)
                    except Exception:self.log('alert_image_error',{'id':event['id']})
                if len(self.pending)>=64:self.store.update_media(event['id'],clip_state='unavailable',media_error='alert_queue_full')
                else:
                    config=self.config();options=config.get('detection') or {}
                    delay=int(options.get('post_seconds',15))+int((config.get('nvr') or {}).get('segment_seconds',10))+5
                    self.pending[event['id']]=(event,event['timestamp']+delay)
            except queue.Empty:pass
            for ident,(event,ready_at) in list(self.pending.items()):
                if ready_at>time.time():continue
                retry=False
                try:
                    self.build(event)
                    current=self.store.get(ident) or {}
                    config=self.config()
                    deadline=event['timestamp']+int((config.get('detection') or {}).get('post_seconds',15))+int((config.get('nvr') or {}).get('segment_seconds',10))+45
                    if current.get('media_error')=='recording_gap_or_disabled' and time.time()<deadline:
                        # A long GOP may delay the last closed segment. Retry
                        # only files; never probe/reconnect to the camera.
                        retry=True;self.pending[ident]=(event,time.time()+3)
                        self.store.update_media(ident,clip_state='pending',media_error=None)
                except Exception:
                    self.store.update_media(ident,clip_state='error',media_error='alert_remux_failed');self.log('alert_clip_error',{'id':ident})
                finally:
                    if not retry:self.pending.pop(ident,None)
                break
            if time.monotonic()-last_prune>60:
                try:self.prune()
                except OSError:pass
                last_prune=time.monotonic()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=2)
