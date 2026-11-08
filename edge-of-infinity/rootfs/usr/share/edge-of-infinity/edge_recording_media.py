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


def aac_encoding_options(codec='', sample_rate=0, channels=1):
    """G.711 voice needs no 48 kHz/96 kbps expansion; keep one audio thread."""
    try:rate=int(sample_rate or 0);count=max(1,min(2,int(channels or 1)))
    except (TypeError,ValueError):rate=0;count=1
    voice=codec in ('pcm_alaw','pcm_mulaw','alaw','ulaw') and rate<=16000
    rate=16000 if voice else rate if rate in (8000,11025,12000,16000,22050,24000,32000,44100,48000) else 48000
    bitrate=32000 if voice else 64000 if rate<=24000 else 96000
    return ['-c:a','aac','-threads:a','1','-b:a',str(bitrate*count),'-ar',str(rate),'-ac',str(count)]


def video_timing(path):
    """Read the video track's sample clock without decoding or spawning FFprobe."""
    path = Path(path)
    stat = path.stat()
    signature = (stat.st_size, stat.st_mtime_ns)
    with _TIMING_LOCK:
        cached = _TIMING.get(str(path))
    if cached and cached[0] == signature:
        return cached[1]
    result = {'fps': 0.0, 'duration': 0.0, 'frame_seconds': 0.0,'audio_codec':'','audio_clock_regular':True,
              'faststart':False,'video_zero_start':False,'audio_zero_start':True}
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
        media_seen=False
        for kind,start,end in boxes(0,stat.st_size):
            if kind==b'mdat':media_seen=True
            if kind!=b'moov':continue
            result['faststart']=not media_seen
            movie_scale=0
            for child,a,b in boxes(start,end):
                if child==b'mvhd':
                    handle.seek(a);data=handle.read(min(32,b-a));offset=20 if data and data[0]==1 else 12
                    movie_scale=int.from_bytes(data[offset:offset+4],'big')
            for child,track_start,track_end in boxes(start,end):
                if child!=b'trak':continue
                edit_time=0;empty_edit=False;leading_seconds=0.0;old_edit_time=0;old_empty_edit=False
                for child,a,b in boxes(track_start,track_end):
                    if child!=b'edts':continue
                    for child,c,d in boxes(a,b):
                        if child!=b'elst':continue
                        handle.seek(c);data=handle.read(min(48,d-c));version=data[0] if data else 0
                        entries=int.from_bytes(data[4:8],'big');width=8 if version==1 else 4
                        edit_time=int.from_bytes(data[8+width:8+width*2],'big',signed=True)
                        empty_edit=entries!=1 or edit_time<0
                        old_edit_time,old_empty_edit=edit_time,empty_edit
                        stride=width*2+4
                        if entries in (1,2) and len(data)>=8+entries*stride:
                            valid=True
                            for i in range(entries):
                                at=8+i*stride;length=int.from_bytes(data[at:at+width],'big');media=int.from_bytes(data[at+width:at+width*2],'big',signed=True)
                                rate=int.from_bytes(data[at+width*2:at+stride],'big')
                                if rate!=65536:valid=False
                                if media==-1 and i<entries-1 and movie_scale:leading_seconds+=length/movie_scale
                                elif media>=0 and i==entries-1:edit_time=media
                                else:valid=False
                            empty_edit=not valid
                for child,media_start,media_end in boxes(track_start,track_end):
                    if child!=b'mdia':continue
                    timescale=duration=0;video=audio=False;table=None;sample_entry='';first_cts=0;sample_data=b'';sync=[]
                    for child,a,b in boxes(media_start,media_end):
                        if child==b'hdlr':
                            handle.seek(a);handler=handle.read(12)[8:12];video=handler==b'vide';audio=handler==b'soun'
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
                                    elif child==b'ctts':
                                        handle.seek(e);data=handle.read(min(16,f-e))
                                        if len(data)==16:first_cts=int.from_bytes(data[12:16],'big',signed=data[0]==1)
                                    elif child==b'stss':
                                        handle.seek(e);data=handle.read(8);n=int.from_bytes(data[4:8],'big')
                                        if n<=100000 and n*4<=f-e-8:sync=[int.from_bytes(handle.read(4),'big') for _ in range(n)]
                                    elif child==b'stsd':
                                        handle.seek(e);data=handle.read(min(80,f-e));sample_data=data
                                        if len(data)>=16 and int.from_bytes(data[4:8],'big'):
                                            sample_entry=data[12:16].decode('ascii',errors='replace')
                    if not (video or audio) or not timescale:continue
                    count=ticks=0;intervals={};runs=[]
                    if table:
                        handle.seek(table[0]);head=handle.read(8)
                        entries=int.from_bytes(head[4:8],'big') if len(head)==8 else 0
                        if entries<=100000 and entries*8<=table[1]-table[0]-8:
                            for _ in range(entries):
                                raw=handle.read(8);n=int.from_bytes(raw[:4],'big');delta=int.from_bytes(raw[4:],'big')
                                count+=n;ticks+=n*delta
                                runs.append((n,delta))
                                if delta>0:intervals[delta]=intervals.get(delta,0)+n
                    # Audio padding / a final sample can extend the average
                    # duration. The weighted median is the video frame clock.
                    accumulated=0;median=0;half=sum(intervals.values())/2
                    for delta,n in sorted(intervals.items()):
                        accumulated+=n
                        if accumulated>=half:median=delta;break
                    if audio:
                        result['audio_codec']=sample_entry
                        result['audio_duration_seconds']=ticks/timescale if ticks else duration/timescale
                        result['audio_start_seconds']=max(0,leading_seconds+(first_cts-edit_time)/timescale) if not empty_edit else None
                        result['audio_zero_start']=not old_empty_edit and first_cts<=old_edit_time
                        if len(sample_data)>=44:
                            result['audio_sample_entry_channels']=int.from_bytes(sample_data[32:34],'big')
                            result['audio_sample_rate']=int.from_bytes(sample_data[40:44],'big')>>16
                        result['audio_clock_regular']=bool(median and runs) and all(delta==median or (i==len(runs)-1 and n==1 and 0<delta<=median) for i,(n,delta) in enumerate(runs))
                        continue
                    fps=timescale/median if median else (timescale*count/ticks if ticks else 0)
                    result.update({'fps':round(fps,6) if .1<=fps<=240 else 0.0,'duration':duration/timescale if duration else 0.0,
                            'video_start_seconds':max(0,leading_seconds+(first_cts-edit_time)/timescale) if not empty_edit else None,
                            'width':int.from_bytes(sample_data[40:42],'big'),'height':int.from_bytes(sample_data[42:44],'big'),
                            'video_zero_start':not old_empty_edit and first_cts==old_edit_time,
                            'keyframe_interval_seconds':round(max((b-a for a,b in zip(sync,sync[1:])),default=count if len(sync)==1 else 1)/fps,3) if fps else None,
                            'frame_seconds':1/fps if .1<=fps<=240 else 0.0,
                            'sample_entry':sample_entry,'codec_name':{'hev1':'hevc','hvc1':'hevc','avc1':'h264','avc3':'h264'}.get(sample_entry,'')})
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
