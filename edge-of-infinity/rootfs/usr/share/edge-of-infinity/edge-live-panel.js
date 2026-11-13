import{EdgeRTCClient}from'../edge-rtc-client.js?v=0.18.0';
import{getDeviceReporter}from'../device-diagnostics.js?v=0.18.0';
try{window.EdgeI18n=parent.EdgeI18n;document.documentElement.dataset.uiMotion=parent.document.documentElement.dataset.uiMotion;}catch(_){}
const languageUI=window.EdgeI18n?.attach(document.body);window.addEventListener('pagehide',()=>languageUI?.close(),{once:true});
const playerBase=new URL(document.baseURI),path=playerBase.pathname.split('/').pop(),url=new URL('../stream/'+path,playerBase),post=data=>parent.postMessage({path,...data},playerBase.origin);
const note=document.querySelector('[data-live-message]'),loader=document.querySelector('[data-live-loading]'),sound=document.querySelector('[data-live-sound]'),microphone=document.querySelector('[data-live-microphone]'),volume=document.querySelector('[data-live-volume]');
const icon=EdgeMediaUI.icon;loader.innerHTML=EdgeMediaUI.logo(new URL('../../brand/icon.png?v=0.18.0',playerBase).href,true);sound.innerHTML=icon('muted');microphone.innerHTML=icon('microphone');
let client,timer,retries=0,muted=true,level=.6,micStream=null,micPending=false,micReply=null,micSerial=0;
const diagnostics=getDeviceReporter({context:{surface:'edge_panel_live',source:path},send:async payload=>{const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),12000);try{const response=await fetch(new URL('../../client-diagnostics',playerBase),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal});if([401,403].includes(response.status)){diagnostics.setPaused?.(true);window.edgeStopLive?.();post({type:'edge_ingress_expired'});}return response.ok;}finally{clearTimeout(timeout);}}});
function soundState(){sound.innerHTML=icon(muted?'muted':'speaker');sound.setAttribute('aria-label',muted?'Włącz dźwięk':'Wycisz');sound.setAttribute('aria-pressed',String(!muted));client?.setAudio(level,muted);}
function stopMic(){micStream?.getTracks().forEach(track=>track.stop());micStream=null;microphone.setAttribute('aria-pressed','false');}
function showError(message){loader.hidden=true;note.hidden=false;note.textContent=message;}
function connect(target=url.href){clearTimeout(timer);client?.close();if(document.hidden)return;loader.hidden=false;note.hidden=true;post({type:'edge_live_state',state:'checking'});
  client=new EdgeRTCClient({url:target,diagnostics,canvas:document.querySelector('canvas'),...(micStream?{microphone:micStream}:{}),onMicrophone:active=>microphone.setAttribute('aria-pressed',String(active)),onError:(message,detail)=>{stopMic();post({type:'edge_live_state',state:'lost_connection'});showError(message);if(detail?.retryable!==false&&!document.hidden&&retries<5)timer=setTimeout(()=>connect(),Math.min(30000,2000*2**retries++));},onFrame:frame=>{retries=0;loader.hidden=true;note.hidden=true;if(frame.frames===1)post({type:'edge_live_ready'});}});
  soundState();client.start().catch(error=>{stopMic();showError(error.message);post({type:'edge_live_state',state:'lost_connection'});});
}
window.edgeStopLive=()=>{clearTimeout(timer);stopMic();micSerial++;if(micReply){clearTimeout(micReply.timer);micReply.reject(Error('microphone_cancelled'));micReply=null;};client?.close();post({type:'edge_live_state',state:'paused'});};
window.edgeSetAudio=(value,enabled)=>{level=Math.max(0,Math.min(1,Number(value)||0));muted=!enabled;volume.value=String(level);soundState();};
window.addEventListener('message',event=>{if(event.origin===playerBase.origin&&event.source===parent&&event.data?.type==='edge_ui_preferences'){document.documentElement.dataset.uiMotion=event.data.motion?'on':'off';return;}if(event.origin!==playerBase.origin||event.source!==parent||event.data?.type!=='edge_live_microphone_reply'||event.data.path!==path||event.data.request!==micReply?.request)return;const pending=micReply;micReply=null;clearTimeout(pending.timer);if(event.data.error)pending.reject(Error(event.data.error));else pending.resolve(event.data.url);});
microphone.onclick=async()=>{
  if(micPending)return;
  if(micStream){stopMic();connect();return;}
  micPending=true;microphone.disabled=true;const serial=++micSerial;let stream;
  try{
    if(!navigator.mediaDevices?.getUserMedia)throw Error('Mikrofon wymaga HTTPS i dostępu z Home Assistanta.');
    stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
    if(serial!==micSerial||document.hidden){stream.getTracks().forEach(track=>track.stop());return;}
    const target=await new Promise((resolve,reject)=>{const request=String(serial)+'-'+Date.now();micReply={request,resolve,reject,timer:setTimeout(()=>{micReply=null;reject(Error('Nie udało się potwierdzić dostępu do mikrofonu w Home Assistant.'));},10000)};post({type:'edge_live_microphone_request',request});});
    if(serial!==micSerial||document.hidden){stream.getTracks().forEach(track=>track.stop());return;}
    const endpoint=new URL(target,playerBase);if(endpoint.origin!==playerBase.origin||!endpoint.pathname.startsWith('/api/edge/rtc/'))throw Error('Nieprawidłowy adres mikrofonu Home Assistant.');
    micStream=stream;connect(endpoint.href);
  }catch(error){stream?.getTracks().forEach(track=>track.stop());showError(error.name==='NotAllowedError'?'Zezwól na mikrofon w przeglądarce, aby rozmawiać przez kamerę.':error.message);}
  finally{micPending=false;microphone.disabled=false;}
};
sound.onclick=()=>{muted=!muted;soundState();};volume.oninput=()=>{level=Number(volume.value);muted=level===0;soundState();};
document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(document.hidden)window.edgeStopLive();else connect();});window.addEventListener('pagehide',()=>window.edgeStopLive());
connect();
