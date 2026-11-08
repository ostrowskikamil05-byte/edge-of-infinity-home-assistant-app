/* Browser-visible diagnostics. Bounded batches; no frame-by-frame network log. */
export function safeDiagnosticURL(value){
 try{const u=new URL(value,location.href);return `${u.protocol}//${u.host}${u.pathname.replace(/\/api\/(?:hassio_ingress|hassio\/ingress)\/[^/]+/g,'/ingress')}`;}catch(_){return '[invalid URL]';}
}
function scrub(value,depth=0){
 if(depth>7)return '[depth limit]';
 if(typeof value==='string')return value.slice(0,1600).replace(/(?:https?|wss?|rtsp):\/\/[^\s"<>]+/g,safeDiagnosticURL).replace(/((?:authsig|access_token|token|password|secret)=)[^&\s]+/gi,'$1***');
 if(Array.isArray(value))return value.slice(0,40).map(v=>scrub(v,depth+1));
 if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).slice(0,48).map(([k,v])=>[k,/password|token|authorization|secret|authsig/i.test(k)?'***':scrub(v,depth+1)]));
 return typeof value==='number'&&!Number.isFinite(value)?null:value;
}
function id(storage,key){
 try{const existing=storage.getItem(key);if(/^[a-f0-9]{32}$/.test(existing||''))return existing;}catch(_){}
 const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);const value=[...bytes].map(v=>v.toString(16).padStart(2,'0')).join('');
 try{storage.setItem(key,value);}catch(_){}return value;
}
export function getDeviceReporter(options={}){
 const shared=globalThis.__edgeDeviceReporter;
 if(shared){shared.configure(options);return shared;}
 const reporter=new DeviceReporter(options);globalThis.__edgeDeviceReporter=reporter;return reporter;
}
class DeviceReporter{
 constructor(options){
  let local,session;try{local=localStorage;session=sessionStorage;}catch(_){}
  this.device_id=id(local,'edge-device-id');this.session_id=id(session,'edge-session-id');
  this.events=[];this.resources=[];this.longTasks=[];this.sources=new Map();this.samplers=new Map();this.dropped=0;this.started=performance.now();this.configure(options);
  try{this.label=localStorage.getItem('edge-device-label')||'';}catch(_){}
  this.note('device_session_start',{navigation:performance.getEntriesByType('navigation').map(e=>({type:e.type,dom_ms:e.domContentLoadedEventEnd,load_ms:e.loadEventEnd,ttfb_ms:e.responseStart-e.requestStart}))});
  if(globalThis.PerformanceObserver){
   try{this.observer=new PerformanceObserver(list=>{for(const e of list.getEntries()){
    if(e.name.includes('client-diagnostics')||e.name.includes('/api/debug'))continue;
    if(this.context?.surface==='home_assistant_card'&&!/\/edge\/|\/api\/edge\//.test(new URL(e.name,location.href).pathname))continue;
    const detailed=!!e.responseStart;
    this.resources.push({url:safeDiagnosticURL(e.name),type:e.initiatorType,duration_ms:Math.round(e.duration),
     dns_ms:detailed?e.domainLookupEnd-e.domainLookupStart:null,tcp_ms:detailed?e.connectEnd-e.connectStart:null,
     tls_ms:detailed&&e.secureConnectionStart?e.connectEnd-e.secureConnectionStart:null,ttfb_ms:detailed?e.responseStart-e.requestStart:null,
     body_ms:detailed?e.responseEnd-e.responseStart:null,transfer_bytes:e.transferSize||null,encoded_bytes:e.encodedBodySize||null,
     protocol:e.nextHopProtocol||null,status:e.responseStatus||null,details_available:detailed});
    if(this.resources.length>24){this.resources.shift();this.dropped++;}
   }});this.observer.observe({type:'resource',buffered:true});}catch(_){}
   try{if(PerformanceObserver.supportedEntryTypes?.includes('longtask')){this.longTaskObserver=new PerformanceObserver(list=>{for(const e of list.getEntries())this.longTasks.push({at_ms:Math.round(e.startTime),duration_ms:Math.round(e.duration)});this.longTasks=this.longTasks.slice(-12);});this.longTaskObserver.observe({type:'longtask'});}}catch(_){}
  }
  for(const name of ['online','offline','visibilitychange'])addEventListener(name,()=>{if(name==='online'){this.retryAfter=0;this.failures=0;}this.note('browser_'+name,{online:navigator.onLine,hidden:document.hidden});if(!document.hidden)this.schedule(500);});
  addEventListener('error',event=>this.note('javascript_error',{message:event.message,source:event.filename?safeDiagnosticURL(event.filename):null,line:event.lineno}));
  addEventListener('unhandledrejection',event=>this.note('promise_rejection',{message:event.reason?.message||String(event.reason)}));
  this.expected=performance.now()+20000;this.timer=setInterval(()=>{this.loopLag=Math.max(0,performance.now()-this.expected);this.expected=performance.now()+20000;if(!document.hidden)this.flush();},20000);
 }
 configure(options){if(options.send)this.send=options.send;if(options.context)this.context=options.context;}
 setLabel(value){this.label=String(value||'').slice(0,64);try{localStorage.setItem('edge-device-label',this.label);}catch(_){}this.note('device_label_changed',{label:this.label});}
 note(event,data={}){this.events.push({event:String(event).slice(0,100),at_ms:Math.round(performance.now()),time:new Date().toISOString(),data:scrub(data)});if(this.events.length>64){this.events.shift();this.dropped++;}this.schedule(1500);}
 source(key,state){this.sources.set(String(key).slice(0,100),{...scrub(state),sampled_at_ms:Math.round(performance.now())});while(this.sources.size>8)this.sources.delete(this.sources.keys().next().value);}
 registerSource(key,sampler){this.samplers.set(key,sampler);while(this.samplers.size>8)this.samplers.delete(this.samplers.keys().next().value);}
 unregisterSource(key){this.samplers.delete(key);}
 schedule(ms){if(!this.flushTimer)this.flushTimer=setTimeout(()=>{this.flushTimer=null;this.flush();},ms);}
 device(){
  try{const saved=localStorage.getItem('edge-device-label');if(saved!==null)this.label=saved;}catch(_){}
  const n=navigator,c=n.connection,v=document.createElement('video');
  return {label:this.label||(/iPhone/.test(n.userAgent)?'iPhone':/iPad/.test(n.userAgent)||n.platform==='MacIntel'&&n.maxTouchPoints>1?'iPad':/Android/.test(n.userAgent)?'Android':'Computer'),
   user_agent:n.userAgent,platform:n.platform,language:n.language,secure_context:isSecureContext,
   viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},screen:{width:screen.width,height:screen.height},touch_points:n.maxTouchPoints,
   reported_cpu_threads:n.hardwareConcurrency||null,reported_device_memory_gb:n.deviceMemory||null,
   js_heap_bytes:performance.memory?.usedJSHeapSize||null,event_loop_lag_ms:Math.round(this.loopLag||0),long_tasks_available:!!this.longTaskObserver,
   measurement_note:'Event-loop delay includes OS/browser throttling. JS heap is a browser estimate, not phone CPU use or free RAM.',
   online:n.onLine,hidden:document.hidden,network:c?{effective_type:c.effectiveType,downlink_mbps:c.downlink,rtt_ms:c.rtt,save_data:c.saveData}:null,
   capabilities:{video_decoder:typeof VideoDecoder==='function',audio_decoder:typeof AudioDecoder==='function',websocket:typeof WebSocket==='function',
    h264_mp4:v.canPlayType('video/mp4; codecs="avc1.640028"'),hevc_mp4:v.canPlayType('video/mp4; codecs="hvc1.1.6.L120.B0"'),aac:v.canPlayType('audio/mp4; codecs="mp4a.40.2"')},
   unavailable:'Phone CPU load, free RAM, Wi-Fi signal and router hops are not exposed by browsers. Device model is a user label, not inferred hardware.'};
 }
 async flush(){
  if(this.busy||!this.send||!navigator.onLine||Date.now()<(this.retryAfter||0))return;
  for(const [key,sampler] of this.samplers){try{this.source(key,sampler());}catch(_){}}
  if(!this.events.length&&!this.resources.length&&![...this.sources.values()].some(s=>s.state!=='closed'))return;
  const events=this.events.splice(0,12),resources=this.resources.splice(0,12),long_tasks=this.longTasks.splice(0,12);
  let data=scrub({device_id:this.device_id,session_id:this.session_id,time_origin_ms:performance.timeOrigin,version:'0.11.12',device:this.device(),context:this.context,
   browser_endpoint:safeDiagnosticURL(location.href),events,resources,long_tasks,sources:Object.fromEntries(this.sources),dropped_entries:this.dropped});
  // Large error payloads must not turn diagnostics into stream traffic.
  while(new TextEncoder().encode(JSON.stringify(data)).length>24000&&(data.events.length||data.resources.length)){
   if(data.events.length>2)data.events.shift();else if(data.resources.length)data.resources.shift();else data.events.shift();this.dropped++;
  }
  data.dropped_entries=this.dropped;
  this.busy=true;
  try{const accepted=await this.send(data);if(accepted===false)throw Error('diagnostics_not_accepted');this.lastSuccess=Date.now();this.failures=0;this.retryAfter=0;}
  catch(_){this.events=[...events,...this.events].slice(-64);this.resources=[...resources,...this.resources].slice(-24);this.failures=Math.min(6,(this.failures||0)+1);const delay=Math.min(120000,2500*2**this.failures);this.retryAfter=Date.now()+delay;this.schedule(delay);}
  finally{this.busy=false;if(this.events.length||this.resources.length)this.schedule(600);}
 }
}
