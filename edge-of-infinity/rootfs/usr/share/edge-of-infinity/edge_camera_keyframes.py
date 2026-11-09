"""Bounded ONVIF synchronization requests; never changes an encoder profile."""
import hashlib
import json
import re
import threading
import time
from urllib.parse import urlparse, parse_qs
from xml.sax.saxutils import escape


def local(tag):
    return tag.rsplit('}', 1)[-1]


def value(root, name):
    return next((n.text or '' for n in root.iter() if local(n.tag) == name), '')


def stream_identity(uri):
    parsed = urlparse(uri)
    query = parse_qs(parsed.query)
    return parsed.path.rstrip('/'), {k: v for k, v in query.items() if k.lower() in ('channel', 'subtype', 'stream', 'profile')}


class CameraKeyframes:
    def __init__(self, config, soap, log, clock=time.monotonic):
        self.config=config;self.soap=soap;self.log=log;self.clock=clock
        self.lock=threading.Lock();self.slots=threading.BoundedSemaphore(2)
        self.busy=set();self.next={};self.profiles={};self.warming=set();self.pending=set()

    def request(self, camera_id, warm_only=False):
        camera=next((c for c in self.config().get('cameras',[]) if c.get('id')==camera_id and c.get('enabled')),None)
        if not camera or not camera.get('onvif_url'):return False
        with self.lock:
            if camera_id in self.warming and not warm_only:
                self.pending.add(camera_id);return True
            if camera_id in self.busy or self.clock()<self.next.get(camera_id,0) or not self.slots.acquire(blocking=False):return False
            self.busy.add(camera_id);self.next[camera_id]=self.clock()+3
            if warm_only:self.warming.add(camera_id)
            while len(self.next)>32:self.next.pop(next(iter(self.next)))
        try:
            threading.Thread(target=self.run,args=(dict(camera),not warm_only),daemon=True).start()
        except RuntimeError:
            with self.lock:self.busy.discard(camera_id);self.warming.discard(camera_id);self.pending.discard(camera_id)
            self.slots.release()
            return False
        return True

    def warm(self):
        for camera in self.config().get('cameras',[]):
            if camera.get('enabled') and camera.get('onvif_url'):self.request(camera['id'],warm_only=True)

    def resolve(self, camera):
        stream=camera.get('record_stream') if camera.get('record_stream') in ('main','sub') else 'main'
        wanted=camera.get('rtsp_'+stream) or camera.get('rtsp_main') or ''
        fingerprint=hashlib.sha256(json.dumps([camera.get(k) for k in ('onvif_url','username','password')]+[wanted]).encode()).hexdigest()
        with self.lock:cached=self.profiles.get(fingerprint)
        if cached and self.clock()-cached[0]<300:return cached[1:]
        deadline=self.clock()+10
        def call(endpoint,action,body):
            if self.clock()>deadline:raise RuntimeError('keyframe_discovery_timeout')
            return self.soap(camera,endpoint,action,body)
        capabilities=call(camera['onvif_url'],'http://www.onvif.org/ver10/device/wsdl/GetCapabilities','<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>')
        media=next((n for n in capabilities.iter() if local(n.tag)=='Media'),None)
        endpoint=(media.get('XAddr') or value(media,'XAddr')) if media is not None else ''
        if not endpoint:raise RuntimeError('keyframe_media_service_unavailable')
        profiles=call(endpoint,'http://www.onvif.org/ver10/media/wsdl/GetProfiles','<trt:GetProfiles/>')
        for profile in [n for n in profiles.iter() if local(n.tag)=='Profiles' and n.get('token')][:8]:
            token=profile.get('token')
            uri=call(endpoint,'http://www.onvif.org/ver10/media/wsdl/GetStreamUri',f'<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup><trt:ProfileToken>{escape(token)}</trt:ProfileToken></trt:GetStreamUri>')
            if stream_identity(value(uri,'Uri'))!=stream_identity(wanted):continue
            with self.lock:
                self.profiles[fingerprint]=(self.clock(),endpoint,token)
                while len(self.profiles)>32:self.profiles.pop(next(iter(self.profiles)))
            return endpoint,token
        raise RuntimeError('keyframe_recording_profile_not_found')

    def run(self, camera, synchronize=True):
        camera_id=camera['id'];started=self.clock();success=False;phase='profile_discovery'
        try:
            endpoint,token=self.resolve(camera)
            with self.lock:
                synchronize=synchronize or camera_id in self.pending
                self.warming.discard(camera_id);self.pending.discard(camera_id)
            phase='synchronization_point'
            if synchronize:self.soap(camera,endpoint,'http://www.onvif.org/ver10/media/wsdl/SetSynchronizationPoint',f'<trt:SetSynchronizationPoint><trt:ProfileToken>{escape(token)}</trt:ProfileToken></trt:SetSynchronizationPoint>')
            success=True
            self.log('keyframe_onvif_requested' if synchronize else 'keyframe_onvif_profile_ready',{'camera_id':camera_id,'elapsed_ms':round((self.clock()-started)*1000),'encoder_settings_changed':False})
        except (RuntimeError,ValueError,OSError) as error:
            text=str(error);code=text if re.fullmatch(r'keyframe_[a-z_]{1,80}',text) else next((name for name in ('ActionNotSupported','NotAuthorized','NoProfile','InvalidArgVal') if name in text),'onvif_request_failed')
            self.log('keyframe_onvif_unavailable',{'camera_id':camera_id,'reason':type(error).__name__,'code':code,'phase':phase,'severity':'warning','optional_capability':True,'encoder_settings_changed':False,'elapsed_ms':round((self.clock()-started)*1000)})
        finally:
            with self.lock:
                self.busy.discard(camera_id)
                self.warming.discard(camera_id);self.pending.discard(camera_id)
                self.next[camera_id]=self.clock()+(60 if not success else 3 if synchronize else 0)
            self.slots.release()
