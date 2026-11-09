"""Explicit camera GOP changes; discovery and ordinary playback remain read-only."""
import copy
import math
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

MEDIA='http://www.onvif.org/ver10/media/wsdl/'

def local(tag):
    return tag.rsplit('}',1)[-1]

def find(root,name):
    return next((node for node in root.iter() if local(node.tag)==name),None)

def stable_fields(root):
    """Check fields which must survive the GOP-only write/readback."""
    result={}
    for parent in root.iter():
        for child in parent:
            key=local(parent.tag)+'/'+local(child.tag)
            if local(child.tag) in ('GovLength','UseCount') or len(child):continue
            value=(child.text or '').strip()
            try:value=float(value)
            except ValueError:pass
            result[key]=value
    return result

def set_onvif_gop(camera,seconds,resolve,soap):
    if seconds not in (1,2):raise ValueError('gop_seconds_must_be_1_or_2')
    endpoint,profile_token=resolve(camera)
    profiles=soap(camera,endpoint,MEDIA+'GetProfiles','<trt:GetProfiles/>')
    profile=next((node for node in profiles.iter() if local(node.tag)=='Profiles' and node.get('token')==profile_token),None)
    embedded=find(profile,'VideoEncoderConfiguration') if profile is not None else None
    token=embedded.get('token') if embedded is not None else None
    if not token:raise ValueError('gop_encoder_configuration_unavailable')
    body=f'<trt:GetVideoEncoderConfiguration><trt:ConfigurationToken>{escape(token)}</trt:ConfigurationToken></trt:GetVideoEncoderConfiguration>'
    def read():
        response=soap(camera,endpoint,MEDIA+'GetVideoEncoderConfiguration',body)
        config=find(response,'Configuration')
        if config is None or config.get('token')!=token:raise ValueError('gop_encoder_readback_unavailable')
        return config
    config=read();before=stable_fields(config);updated=copy.deepcopy(config)
    rate=find(updated,'FrameRateLimit');gov=find(updated,'GovLength')
    if rate is None or gov is None:raise ValueError('gop_not_exposed_by_camera')
    fps=float(rate.text or 0)
    if not math.isfinite(fps) or not 1<=fps<=240:raise ValueError('gop_invalid_camera_fps')
    frames=max(1,round(fps*seconds));gov.text=str(frames)
    config_xml=ET.tostring(updated,encoding='unicode')
    soap(camera,endpoint,MEDIA+'SetVideoEncoderConfiguration','<trt:SetVideoEncoderConfiguration>'+config_xml+'<trt:ForcePersistence>true</trt:ForcePersistence></trt:SetVideoEncoderConfiguration>')
    verified=read();actual=find(verified,'GovLength')
    if actual is None or int(actual.text or 0)!=frames or stable_fields(verified)!=before:raise ValueError('gop_camera_readback_mismatch')
    return {'status':'saved','seconds':seconds,'frames':frames,'fps':fps,'profile':camera.get('record_stream') or 'main','encoder_token':token}
