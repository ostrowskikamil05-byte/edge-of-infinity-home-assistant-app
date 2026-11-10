/* EdgeRTC/1: H264/H265 access units -> WebCodecs. No RTCPeerConnection, MP4 or MSE. */
export function edgeRTCPlaybackSupport(language=globalThis.navigator?.language||'en'){
  const pl=language.startsWith('pl');
  if(!globalThis.isSecureContext)return{supported:false,code:'insecure_context',message:pl?'EdgeRTC: otwórz Home Assistant przez HTTPS, np. adresem https://…ui.nabu.casa z Home Assistant Cloud. HTTP w sieci lokalnej blokuje WebCodecs.':'EdgeRTC: open Home Assistant over HTTPS, for example your https://…ui.nabu.casa Home Assistant Cloud address. Local-network HTTP blocks WebCodecs.'};
  if(typeof globalThis.VideoDecoder!=='function'||typeof globalThis.EncodedVideoChunk!=='function')return{supported:false,code:'webcodecs_unavailable',message:pl?'EdgeRTC: ta przeglądarka nie udostępnia dekodera WebCodecs. Otwórz adres HTTPS Home Assistanta w aktualnym Chrome lub Edge.':'EdgeRTC: this browser does not provide a WebCodecs decoder. Open the Home Assistant HTTPS address in an up-to-date Chrome or Edge browser.'};
  return{supported:true,code:'supported',message:''};
}

export class EdgeRTCClient {
  constructor(options){this.options=options;this.canvas=options.canvas;this.closed=false;this.playing=false;this.connectionState='connecting';this.waitKey=true;this.firstFrameMS=null;this.frames=0;this.started=performance.now();this.frameQueue=[];this.droppedFrames=0;this.bootstrapFrames=0;}
  async start(){
    this.diagnosticKey=this.options.entity||(this.options.url?new URL(this.options.url,location.href).pathname.split('/').pop():'edge_rtc');this.diagnostics=this.options.diagnostics;
    this.diagnostics?.registerSource(this.diagnosticKey,()=>this.diagnosticState());
    this.diagnostics?.note('rtc_connect_start',{source:this.diagnosticKey});
    this.language=this.options.language||this.options.hass?.locale?.language||this.options.hass?.language||globalThis.navigator?.language||'en';const support=edgeRTCPlaybackSupport(this.language);
    if(!support.supported){const error=new Error(support.message);error.code=support.code;error.retryable=false;this.diagnostics?.note('rtc_support_missing',{source:this.diagnosticKey,code:support.code});this.close();throw error;}
    this.connectTimer=setTimeout(()=>this.fail('EdgeRTC camera timed out.'),15000);let url=this.options.url;
    if(!url){const path=`/api/edge/rtc/${this.options.entity}?microphone=${this.options.microphone?1:0}`,before=performance.now();const signed=await this.options.hass.callWS({type:'auth/sign_path',path});this.diagnostics?.note('rtc_ha_sign_complete',{source:this.diagnosticKey,elapsed_ms:performance.now()-before});if(this.closed)return;url=this.options.hass.hassUrl?this.options.hass.hassUrl(signed.path):new URL(signed.path,location.href).href;}
    if(this.closed)return;url=url.replace(/^http:/,'ws:').replace(/^https:/,'wss:');
    this.diagnostics?.note('rtc_transport_target',{source:this.diagnosticKey,endpoint:url});this.ws=new WebSocket(url);this.ws.binaryType='arraybuffer';this.messageChain=Promise.resolve();this.pendingMessages=0;this.pendingBytes=0;this.receivedBytes=0;
    this.ws.onopen=()=>{this.websocketOpenAt=performance.now();this.diagnostics?.note('rtc_websocket_open',{source:this.diagnosticKey,open_ms:performance.now()-this.started});};
    this.ws.onmessage=e=>this.queueMessage(e.data);this.ws.onerror=()=>this.fail('EdgeRTC connection failed.');this.ws.onclose=e=>{this.diagnostics?.note('rtc_websocket_close',{source:this.diagnosticKey,code:e?.code,clean:e?.wasClean,expected:this.closed});if(!this.closed)this.fail('EdgeRTC source disconnected.');};
  }
  queueMessage(data){
    if(this.closed)return;
    const size=data.byteLength??data.length??0;this.receivedBytes+=size;this.lastPacketAt=performance.now();
    const bytes=data instanceof ArrayBuffer&&size>=10?new Uint8Array(data):null;
    const video=bytes?.[0]===1,stamp=video?Number(new DataView(data).getBigInt64(2)):null;
    if(this.options.audioPlaybackMode!=='archive')this.observeDelivery(size,stamp);
    if(video&&this.firstVideoPacketAt==null)this.firstVideoPacketAt=performance.now();
    // A newly arrived IDR makes older queued references unnecessary. Prefer
    // it before decoding hundreds of cached 4K pictures; steady live is intact.
    if(this.firstFrameMS===null&&video&&(bytes[1]&1)&&(this.latestBootstrapKey==null||stamp>this.latestBootstrapKey)){
      this.latestBootstrapKey=stamp;
      this.bootstrapFloorTimestamp=Math.max(this.bootstrapFloorTimestamp??stamp,stamp);
      this.bootstrapWake?.(true);
    }
    // Recover only at a complete IDR. Dropping arbitrary encoded deltas would
    // break their references and freeze a long-GOP camera until the next IDR.
    if(this.caughtUp&&video&&(bytes[1]&1)&&stamp-(this.lastPresentedTimestamp??stamp)>1000000&&this.pendingMessages>24){
      this.liveRecoveryKey=stamp;this.bootstrapWake?.(true);
    }
    this.pendingMessages++;this.pendingBytes+=size;
    if(this.pendingMessages>(this.caughtUp?256:4096)||this.pendingBytes>24*1024*1024){this.pendingMessages--;this.pendingBytes-=size;this.fail('EdgeRTC decoder cannot keep up.');return;}
    this.messageChain=this.messageChain.then(()=>{
      if(this.firstFrameMS===null&&video&&this.latestBootstrapKey!=null&&stamp<this.latestBootstrapKey){this.bootstrapSkippedPackets=(this.bootstrapSkippedPackets||0)+1;return;}
      if(bytes&&this.liveRecoveryKey!=null&&Number(new DataView(data).getBigInt64(2))<this.liveRecoveryKey){this.networkSupersededPackets=(this.networkSupersededPackets||0)+1;return;}
      return this.message(data);
    }).catch(error=>this.fail(error.message)).finally(()=>{this.pendingMessages--;this.pendingBytes-=size;});
  }
  observeDelivery(size,stamp){
    const now=performance.now();
    this.deliveryWindow??={at:now,bytes:0};this.deliveryWindow.bytes+=size;
    if(now-this.deliveryWindow.at>=1000){this.deliveryBPS=Math.round(this.deliveryWindow.bytes*8000/(now-this.deliveryWindow.at));this.deliveryWindow={at:now,bytes:0};}
    if(!this.caughtUp||stamp==null)return;
    // Use increasing source timestamps so B-frame ordering is not mistaken
    // for transport jitter. Ignore bootstrap and source clock discontinuities.
    const previous=this.networkArrival;
    if(previous&&stamp<=previous.stamp)return;
    this.networkArrival={at:now,stamp};
    if(!previous)return;
    const sourceGap=(stamp-previous.stamp)/1000,arrivalGap=now-previous.at;
    if(sourceGap<=0||sourceGap>1000||arrivalGap>5000)return;
    const excess=Math.max(0,arrivalGap-sourceGap);
    this.networkJitterMS=(this.networkJitterMS||0)*.9+Math.abs(arrivalGap-sourceGap)*.1;
    // Leave headroom in fewer steps: chasing each 20 ms fluctuation causes
    // repeated tiny refills on cellular links even though the trend is stable.
    if(excess>80)this.setNetworkBuffer(Math.ceil((excess+40)/80)*80,'delivery_gap');
  }
  setNetworkBuffer(milliseconds,reason){
    if(this.closed||this.options.audioPlaybackMode==='archive')return;
    const target=Math.min(400,Math.max(0,milliseconds));
    if(target<=(this.networkBufferMS||0))return;
    const delta=target-(this.networkBufferMS||0);this.networkBufferMS=target;
    for(const item of this.frameQueue)item.due+=delta;
    if(this.lastScheduled!=null)this.lastScheduled=this.frameQueue.length?this.lastScheduled+delta:Math.max(this.lastScheduled+delta,performance.now()+target-(this.frameInterval||0));
    this.audioPlayback?.port.postMessage({type:'network',target_ms:target});
    this.diagnostics?.note('rtc_network_buffer',{source:this.diagnosticKey,target_ms:target,reason,jitter_ms:Math.round(this.networkJitterMS||0),delivery_bps:this.deliveryBPS,video_changed:false});
  }
  networkPing(){
    if(this.closed||this.ws?.readyState!==WebSocket.OPEN||this.ws.bufferedAmount>1024)return;
    if(this.networkPingPending&&performance.now()-this.networkPingPending.at<15000)return;
    const id=this.networkPingID=(this.networkPingID||0)%4294967295+1;
    this.networkPingPending={id,at:performance.now()};this.ws.send(JSON.stringify({type:'network_ping',id}));
  }
  fail(message,retryable=true){if(this.closed)return;this.diagnostics?.note('rtc_error',{source:this.diagnosticKey,message,retryable});this.options.onError?.(message,{retryable});this.close();}
  diagnosticState(){return {state:this.closed?'closed':this.playing?'playing':this.connectionState,codec:this._codec,width:this.width,height:this.height,
    first_frame_ms:this.firstFrameMS,frames:this.frames,dropped_frames:this.droppedFrames,source_fps:this.frameInterval?1000/this.frameInterval:null,
    decode_queue:this.video?.decodeQueueSize,render_queue:this.frameQueue.length,render_ahead_ms:Math.max(0,(this.frameQueue.at(-1)?.due||0)-performance.now()),render_limit_ms:250+2*(this.networkBufferMS||0),message_queue:this.pendingMessages,received_bytes:this.receivedBytes,
    last_packet_age_ms:this.lastPacketAt?performance.now()-this.lastPacketAt:null,websocket_outgoing_buffer_bytes:this.ws?.bufferedAmount,
    bootstrap_packets:this.bootstrapPackets||0,bootstrap_frames_skipped:this.bootstrapFrames,bootstrap_wait_ms:this.bootstrapWaitMS||0,source_metadata_wait_ms:this.sourceMetadataWaitMS||0,decoder_wait_ms:this.decoderWaitMS||0,message_bytes:this.pendingBytes||0,
    websocket_open_ms:this.websocketOpenAt==null?null:this.websocketOpenAt-this.started,decoder_configured_ms:this.decoderConfiguredAt==null?null:this.decoderConfiguredAt-this.started,first_video_packet_ms:this.firstVideoPacketAt==null?null:this.firstVideoPacketAt-this.started,bootstrap_decode_ms:this.decoderConfiguredAt==null||this.firstFrameMS===null?null:this.firstFrameMS-(this.decoderConfiguredAt-this.started),
    bootstrap_packets_superseded:this.bootstrapSkippedPackets||0,bootstrap_keyframe_replacements:this.bootstrapKeyReplacements||0,
    network_buffer_ms:this.networkBufferMS||0,network_jitter_ms:Math.round(this.networkJitterMS||0),network_rtt_ms:this.networkRTTMS??null,delivery_bps:this.deliveryBPS??null,server_queue_bytes:this.networkServerQueueBytes??null,network_recoveries:this.networkRecoveries||0,network_superseded_packets:this.networkSupersededPackets||0,
    measured_end_to_end_latency_ms:null,audio_state:this.audio?.state||'muted_or_not_started',audio_codec:this.audioInfo?.codec,audio_rate:this.audioInfo?.sampleRate,
    audio_queue_ms:Math.round((this.audioQueuedSeconds||0)*1000)+(this.audioStats?.queued_ms||0),audio_scheduled_ms:0,audio_presentation:'continuous_pcm_worklet',audio_output_rate:this.audio?.sampleRate,audio_output_frames:this.audioStats?.output_frames||0,
    audio_packets:this.audioPackets||0,audio_dropped_packets:this.audioDroppedPackets||0,audio_underruns:this.audioStats?.underruns||0,audio_overruns:this.audioStats?.overruns||0,audio_dropped_frames:this.audioStats?.dropped_frames||0,audio_worklet_mode:this.audioStats?.mode,audio_buffer_limit_ms:this.audioStats?.buffer_limit_ms};}
  async message(data){
    if(this.closed)return;
    try{
      if(typeof data==='string'){
        const msg=JSON.parse(data);if(msg.type==='error'){this.fail(`EdgeRTC: ${msg.code}`);return;}
        if(msg.type==='network_pong'){
          if(msg.id===this.networkPingPending?.id){this.networkRTTMS=Math.round(performance.now()-this.networkPingPending.at);this.networkPingPending=null;this.networkServerQueueBytes=msg.queue_bytes;this.diagnostics?.note('rtc_network_sample',{source:this.diagnosticKey,...this.diagnosticState()});}return;
        }
        if(msg.type!=='hello'||msg.protocol!=='edgertc/1')return;
        this.bootstrapTimestamp=Number.isFinite(msg.bootstrap_timestamp_us)?msg.bootstrap_timestamp_us:null;this.sourceMetadataWaitMS=Number(msg.metadata_wait_ms||0);this.audioInfo=msg.audio;this.microphoneSupported=Boolean(msg.microphone?.supported);
        this.bootstrapFloorTimestamp=this.latestBootstrapKey==null?this.bootstrapTimestamp:Math.max(this.bootstrapTimestamp??this.latestBootstrapKey,this.latestBootstrapKey);this.bootstrapPackets=Number(msg.bootstrap_packets||0);
        this.diagnostics?.note('rtc_bootstrap',{source:this.diagnosticKey,packets:this.bootstrapPackets,bytes:msg.bootstrap_bytes||0,refreshed_before_send:Boolean(msg.bootstrap_refreshed),has_keyframe:this.bootstrapTimestamp!==null});
        const configuration={codec:msg.video.codec,optimizeForLatency:true,hardwareAcceleration:'prefer-hardware'};
        let supported;
        try{supported=await VideoDecoder.isConfigSupported(configuration);}catch(_){supported={supported:false};}
        if(!supported.supported){configuration.hardwareAcceleration='no-preference';Object.assign(supported,await VideoDecoder.isConfigSupported(configuration));}
        if(this.closed)return;
        if(!supported.supported){const hevc=/^(hev1|hvc1)\./.test(msg.video.codec);this.fail(hevc?'EdgeRTC: this browser/device cannot decode this H.265 profile. Select H.264 in the camera recording profile, or use a device/browser with HEVC decoding. H.265 recording remains available.':'EdgeRTC: this browser cannot decode the camera H.264 profile.',false);return;}
        this.video=new VideoDecoder({output:frame=>this.render(frame),error:error=>this.fail('EdgeRTC video decoder: '+error.message)});
        this.decoderConfiguredAt=performance.now();this._codec=msg.video.codec;this.decoderConfiguration=configuration;this.videoCodecName=msg.video.codec_name||(/^(hev1|hvc1)\./.test(msg.video.codec)?'h265':'h264');this.video.configure(configuration);
        this.diagnostics?.note('rtc_decoder_configured',{source:this.diagnosticKey,codec:this._codec,requested_acceleration:configuration.hardwareAcceleration,audio_codec:msg.audio?.codec,audio_rate:msg.audio?.sampleRate,microphone_supported:this.microphoneSupported});
        this.connectionState='connected';this.options.onReady?.();
        if(msg.network_feedback){this.networkPingTimer=setInterval(()=>this.networkPing(),5000);this.networkPing();}
        if(this.options.microphone){if(!msg.microphone?.active)throw new Error('Camera microphone is unavailable.');await this.startMicrophone();}
        return;
      }
      const bytes=new Uint8Array(data);if(bytes.length<10)return;const timestamp=Number(new DataView(data).getBigInt64(2));const payload=bytes.subarray(10);
      if(bytes[0]===1&&this.video?.state==='configured'){
        const key=Boolean(bytes[1]&1);if(key)this.waitKey=false;if(this.waitKey)return;
        if(key&&this.liveRecoveryKey!=null&&timestamp>=this.liveRecoveryKey&&this.lastRecoveryKey!==this.liveRecoveryKey){
          this.clearFrames();this.clearAudio();if(this.audioDecoder?.state!=='closed')this.audioDecoder?.close();this.audioDecoder=null;
          this.video.reset();this.video.configure(this.decoderConfiguration);this.lastRecoveryKey=this.liveRecoveryKey;this.networkRecoveries=(this.networkRecoveries||0)+1;
          this.diagnostics?.note('rtc_network_recovery',{source:this.diagnosticKey,keyframe_us:timestamp,skipped_packets:this.networkSupersededPackets||0});
        }
        if(key&&this.firstFrameMS===null){
          if(this.decodedBootstrapKey!=null&&timestamp>this.decodedBootstrapKey){
            this.clearFrames();this.video.reset();this.video.configure(this.decoderConfiguration);this.bootstrapKeyReplacements=(this.bootstrapKeyReplacements||0)+1;
            this.diagnostics?.note('rtc_bootstrap_replaced',{source:this.diagnosticKey,timestamp_us:timestamp,superseded_packets:this.bootstrapSkippedPackets||0});
          }
          this.decodedBootstrapKey=timestamp;
        }
        // A slow decoder cannot accumulate seconds of old frames.
        // A normal TCP burst can contain more than eight valid pictures.
        // Wait for decoder capacity; resetting on a delta discards its
        // references and can freeze a 24-second-GOP camera until the next IDR.
        if(!await this.waitForBootstrapCapacity(timestamp))return;
        this.video.decode(new EncodedVideoChunk({type:key?'key':'delta',timestamp,data:payload}));
      }else if(bytes[0]===2&&!this.muted)this.decodeAudio(payload,timestamp);
    }catch(error){this.fail(error.message||'EdgeRTC decoding failed.');}
  }
  clearFrames(){
    if(this.renderRequest!=null)cancelAnimationFrame(this.renderRequest);
    this.renderRequest=null;for(const item of this.frameQueue)item.frame.close();this.frameQueue=[];this.lastScheduled=null;this.lastTimestamp=null;
  }
  async waitForBootstrapCapacity(timestamp){
    const started=performance.now();
    while(!this.closed&&this.video?.state==='configured'&&this.video.decodeQueueSize>=8){
      if(this.firstFrameMS===null&&timestamp!=null&&this.latestBootstrapKey!=null&&timestamp<this.latestBootstrapKey)return false;
      if(timestamp!=null&&this.liveRecoveryKey!=null&&timestamp<this.liveRecoveryKey)return false;
      const ready=await new Promise(resolve=>{
        const decoder=this.video;let timer;
        const finish=value=>{clearTimeout(timer);decoder.removeEventListener('dequeue',dequeue);if(this.bootstrapWake===finish)this.bootstrapWake=null;resolve(value);};
        const dequeue=()=>finish(true);this.bootstrapWake=finish;decoder.addEventListener('dequeue',dequeue,{once:true});timer=setTimeout(()=>finish(false),5000);
        if(this.closed||decoder.state!=='configured'||decoder.decodeQueueSize<8)finish(!this.closed);
      });
      if(!ready){if(!this.closed)throw Error('EdgeRTC video decoder stalled.');return false;}
    }
    if(this.firstFrameMS===null)this.bootstrapWaitMS=(this.bootstrapWaitMS||0)+performance.now()-started;else this.decoderWaitMS=(this.decoderWaitMS||0)+performance.now()-started;
    return !this.closed&&this.video?.state==='configured'&&!(this.firstFrameMS===null&&timestamp!=null&&this.latestBootstrapKey!=null&&timestamp<this.latestBootstrapKey)&&!(timestamp!=null&&this.liveRecoveryKey!=null&&timestamp<this.liveRecoveryKey);
  }
  render(frame){
    if(this.closed){frame.close();return;}
    const floor=this.liveRecoveryKey??this.bootstrapFloorTimestamp??this.bootstrapTimestamp;
    if(floor!==null&&floor!==undefined&&frame.timestamp<floor){this.bootstrapFrames++;frame.close();return;}
    this.bootstrapTimestamp=null;
    const now=performance.now(),timestamp=Number(frame.timestamp);
    const interval=this.lastTimestamp==null?0:(timestamp-this.lastTimestamp)/1000;
    if(interval>0&&interval<1000)this.frameInterval=this.frameInterval==null?interval:this.frameInterval*.9+interval*.1;
    const target=this.networkBufferMS||0,limit=250+2*target;
    const due=interval>0&&interval<1000?Math.max(now,(this.lastScheduled??now+target)+interval):now+target;
    this.lastTimestamp=timestamp;this.lastScheduled=due;
    this.frameQueue.push({frame,due});
    // Normal 20/30 FPS bursts are paced by camera timestamps. Bound latency
    // when the tab/device falls behind; never synthesize or resize a frame.
    const frameLimit=target?Math.min(32,Math.max(8,Math.ceil(limit/(this.frameInterval||33))+1)):8;
    while(this.frameQueue.length>frameLimit||this.frameQueue.length>1&&(this.frameQueue.at(-1).due-this.frameQueue[0].due>limit+1||this.frameQueue[0].due<now-limit)){this.frameQueue.shift().frame.close();this.droppedFrames++;}
    if(this.frameQueue.length&&this.frameQueue.at(-1).due>now+limit+1){
      // Rebase the retained window without compressing its frame intervals.
      // Four 20 FPS pictures span 150 ms: moving the newest one to "now"
      // used to play the whole burst at display refresh speed, then freeze.
      const shift=target?this.frameQueue.at(-1).due-now-limit:this.frameQueue[0].due-now;
      for(const item of this.frameQueue)item.due-=shift;this.lastScheduled-=shift;
    }
    if(this.renderRequest==null)this.renderRequest=requestAnimationFrame(()=>this.drawFrame());
  }
  drawFrame(){
    this.renderRequest=null;if(this.closed)return;
    const now=performance.now();
    while(this.frameQueue.length&&this.firstFrameMS===null&&this.bootstrapFloorTimestamp!=null&&this.frameQueue[0].frame.timestamp<this.bootstrapFloorTimestamp){this.frameQueue.shift().frame.close();this.bootstrapFrames++;}
    while(this.frameQueue.length>1&&this.frameQueue[0].due<now-250-2*(this.networkBufferMS||0)){this.frameQueue.shift().frame.close();this.droppedFrames++;}
    const item=this.frameQueue[0];
    if(item&&item.due<=now+1){
      this.frameQueue.shift();const current=item.frame;
      try{
        this.width=current.displayWidth;this.height=current.displayHeight;
        if(this.canvas.width!==this.width||this.canvas.height!==this.height){this.canvas.width=this.width;this.canvas.height=this.height;}
        this.context??=this.canvas.getContext('2d',{alpha:false,desynchronized:true});
        this.context.drawImage(current,0,0,this.width,this.height);
      }finally{current.close();}
      clearTimeout(this.connectTimer);this.playing=true;this.frames++;this.lastPresentedTimestamp=Number(current.timestamp);
      if((this.video?.decodeQueueSize??0)<3&&(this.pendingMessages??0)<32)this.caughtUp=true;
      if(this.firstFrameMS===null)this.firstFrameMS=now-this.started;
      if(this.diagnostics&&(!this.lastDiagnosticAt||now-this.lastDiagnosticAt>=1000)){
        const state=this.diagnosticState();
        this.lastDiagnosticAt=now;this.diagnostics.source(this.diagnosticKey,state);if(this.frames===1)this.diagnostics.note('rtc_first_frame',{source:this.diagnosticKey,...state});
      }
      this.options.onFrame?.({width:this.width,height:this.height,firstFrameMS:this.firstFrameMS,frames:this.frames,sourceFPS:this.frameInterval?1000/this.frameInterval:0,droppedFrames:this.droppedFrames,bootstrapFrames:this.bootstrapFrames,queuedFrames:this.frameQueue.length});
    }
    if(this.frameQueue.length)this.renderRequest=requestAnimationFrame(()=>this.drawFrame());
  }
  ensureAudio(){if(!this.audio){try{this.audio=new AudioContext({latencyHint:'interactive',sampleRate:48000});}catch(_){this.audio=new AudioContext({latencyHint:'interactive'});}this.gain=this.audio.createGain();this.gain.connect(this.audio.destination);}this.gain.gain.value=this.muted?0:this.volume??0.6;this.audio.resume().catch(()=>{});return this.audio;}
  setAudio(volume,muted){this.volume=volume;if(muted&&!this.muted){this.clearAudio();if(this.audioDecoder?.state!=='closed')this.audioDecoder?.close();this.audioDecoder=null;}if(!muted&&this.muted){this.audioDisabled=false;if(this.audioPlaybackFailed){this.audioPlaybackPromise=null;this.audioModulePromise=null;this.audioPlaybackFailed=false;}}this.muted=muted;if(!muted||this.audio)this.ensureAudio();if(!muted)this.startAudioPlayback();}
  clearAudio(){this.audioPlayback?.port.postMessage({type:'clear'});this.audioPending=[];this.audioQueuedSeconds=0;this.audioStats=null;}
  decodeAudio(payload,timestamp){
    const info=this.audioInfo;if(!info||this.audioDisabled)return;const audio=this.ensureAudio();
    if(info.codec==='pcma'||info.codec==='pcmu'){
      const out=new Float32Array(payload.length);
      for(let i=0;i<payload.length;i++){let v=payload[i],sample;if(info.codec==='pcmu'){v=~v&255;sample=(((v&15)<<3)+132)<<((v>>4)&7);sample=(v&128)?132-sample:sample-132;}else{v^=85;sample=(v&15)<<4;const exponent=(v>>4)&7;sample+=exponent?264:8;if(exponent>1)sample<<=exponent-1;if(!(v&128))sample=-sample;}out[i]=sample/32768;}this.playPCM([out],8000,timestamp);return;
    }
    if(!window.AudioDecoder){if(!this.audioUnavailableLogged){this.audioUnavailableLogged=true;this.diagnostics?.note('rtc_audio_decoder_missing',{source:this.diagnosticKey,codec:info.codec});}return;}
    if(!this.audioDecoder){this.audioDecoder=new AudioDecoder({output:data=>{if(this.closed){data.close();return;}const timestamp=data.timestamp,rate=data.sampleRate,channels=Array.from({length:Math.min(2,data.numberOfChannels)},(_,i)=>{const out=new Float32Array(data.numberOfFrames);data.copyTo(out,{planeIndex:i,format:'f32-planar'});return out;});data.close();this.playPCM(channels,rate,timestamp);},error:error=>{this.diagnostics?.note('rtc_audio_error',{source:this.diagnosticKey,message:error.message,codec:info.codec});if(this.audioDecoder?.state!=='closed')this.audioDecoder?.close();this.audioDecoder=null;this.audioDisabled=true;}});const config={codec:info.codec,sampleRate:info.sampleRate,numberOfChannels:info.channels};if(info.config)config.description=Uint8Array.from(atob(info.config),c=>c.charCodeAt(0));this.audioDecoder.configure(config);}
    if(this.audioDecoder.decodeQueueSize<12)this.audioDecoder.decode(new EncodedAudioChunk({type:'key',timestamp,data:payload}));
  }
  audioModule(){
    return this.audioModulePromise??=this.audio.audioWorklet.addModule(this.options.audioWorkletURL||new URL('./edge-pcm-worklet.js?v=0.15.1',import.meta.url).href);
  }
  startAudioPlayback(){
    if(this.audioPlaybackPromise||this.closed||this.audioDisabled)return;
    const started=performance.now();
    this.audioPlaybackPromise=Promise.resolve().then(()=>this.audioModule()).then(()=>{
      if(this.closed)return;
      this.audioPlayback=new AudioWorkletNode(this.audio,'edge-playback',{numberOfInputs:0,numberOfOutputs:1,outputChannelCount:[2],processorOptions:{mode:this.options.audioPlaybackMode==='archive'?'archive':'live',network_target_ms:this.networkBufferMS||0}});
      this.audioPlayback.connect(this.gain);
      this.audioPlayback.port.onmessage=event=>{
        if(this.closed)return;const data=event.data;
        if(data.type==='started'&&!this.audioStarted){this.audioStarted=true;this.diagnostics?.note('rtc_audio_started',{source:this.diagnosticKey,codec:this.audioInfo?.codec,output_rate:this.audio.sampleRate,module_ms:Math.round(performance.now()-started)});}
        if(data.type==='stats'){
          this.audioStats=data;this.audioStatsAt=performance.now();
          const resync=data.underruns+data.overruns;
          if(resync!==(this.audioReportedResync||0)&&performance.now()-(this.audioReportedAt||-10000)>5000){this.audioReportedAt=performance.now();this.audioReportedResync=resync;this.diagnostics?.note('rtc_audio_resync',{source:this.diagnosticKey,...this.diagnosticState()});}
        }
      };
      const pending=this.audioPending||[];this.audioPending=[];this.audioQueuedSeconds=0;
      if(!this.muted)for(const packet of pending)this.sendPCM(packet);
    }).catch(error=>{if(this.closed)return;this.audioDisabled=true;this.audioPlaybackFailed=true;this.audioPending=[];this.audioQueuedSeconds=0;this.diagnostics?.note('rtc_audio_error',{source:this.diagnosticKey,stage:'playback_worklet',message:error.message});});
  }
  sendPCM(packet){this.audioPlayback.port.postMessage(packet,packet.channels.map(channel=>channel.buffer));}
  playPCM(channels,rate,timestamp){
    if(this.closed||this.muted||this.audioDisabled||this.audio.state!=='running')return;
    const duration=channels[0].length/rate;if(!Number.isFinite(duration)||duration<=0||duration>.25){this.audioDroppedPackets=(this.audioDroppedPackets||0)+1;return;}
    this.audioPackets=(this.audioPackets||0)+1;const packet={type:'pcm',channels,rate,timestamp};
    if(this.audioPlayback){this.sendPCM(packet);return;}
    this.audioPending??=[];this.audioPending.push(packet);this.audioQueuedSeconds=(this.audioQueuedSeconds||0)+duration;
    const limit=this.options.audioPlaybackMode==='archive' ? .6 : .24+2*(this.networkBufferMS||0)/1000;
    while(this.audioPending.length>1&&(this.audioQueuedSeconds>limit||this.audioPending.length>48)){const old=this.audioPending.shift();this.audioQueuedSeconds-=old.channels[0].length/old.rate;this.audioDroppedPackets=(this.audioDroppedPackets||0)+1;}
    this.startAudioPlayback();
  }
  async startMicrophone(){this.micTimer=setTimeout(()=>this.fail('Microphone initialization timed out.'),10000);for(const track of this.options.microphone.getTracks())track.addEventListener('ended',()=>this.fail('Microphone device disconnected.'),{once:true});const audio=this.ensureAudio();await this.audioModule();if(this.closed)return;this.micSource=audio.createMediaStreamSource(this.options.microphone);this.worklet=new AudioWorkletNode(audio,'edge-pcm');this.silence=audio.createGain();this.silence.gain.value=0;this.micSource.connect(this.worklet);this.worklet.connect(this.silence);this.silence.connect(audio.destination);this.worklet.port.onmessage=event=>{if(this.closed||this.ws.readyState!==WebSocket.OPEN)return;if(this.ws.bufferedAmount>6400){this.fail('Microphone connection is too slow.');return;}const packet=new Uint8Array(321);packet[0]=3;packet.set(new Uint8Array(event.data),1);this.ws.send(packet);};clearTimeout(this.micTimer);this.options.onMicrophone?.(true);}
  close(){if(this.closed)return;this.diagnostics?.unregisterSource(this.diagnosticKey);this.diagnostics?.source(this.diagnosticKey,{state:'closed',frames:this.frames,dropped_frames:this.droppedFrames,first_frame_ms:this.firstFrameMS});this.diagnostics?.note('rtc_client_closed',{source:this.diagnosticKey,frames:this.frames});this.closed=true;this.bootstrapWake?.(false);clearTimeout(this.connectTimer);clearTimeout(this.micTimer);clearInterval(this.networkPingTimer);this.options.microphone?.getTracks().forEach(track=>track.stop());this.playing=false;this.connectionState='closed';this.ws?.close();if(this.video?.state!=='closed')this.video?.close();if(this.audioDecoder?.state!=='closed')this.audioDecoder?.close();this.clearFrames();this.clearAudio();this.audioPlayback?.disconnect();this.audioPlayback?.port.close();this.worklet?.port.close();this.micSource?.disconnect();this.worklet?.disconnect();this.silence?.disconnect();this.audio?.close().catch(()=>{});}
}
