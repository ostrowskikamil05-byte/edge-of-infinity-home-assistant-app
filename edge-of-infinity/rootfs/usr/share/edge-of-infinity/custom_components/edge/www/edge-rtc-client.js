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
    this.diagnostics?.note('rtc_transport_target',{source:this.diagnosticKey,endpoint:url});this.ws=new WebSocket(url);this.ws.binaryType='arraybuffer';this.messageChain=Promise.resolve();this.pendingMessages=0;this.receivedBytes=0;
    this.ws.onopen=()=>this.diagnostics?.note('rtc_websocket_open',{source:this.diagnosticKey,open_ms:performance.now()-this.started});
    this.ws.onmessage=e=>{if(this.closed)return;this.receivedBytes+=e.data.byteLength??e.data.length??0;this.lastPacketAt=performance.now();if(++this.pendingMessages>256){this.fail('EdgeRTC decoder cannot keep up.');return;}this.messageChain=this.messageChain.then(()=>this.message(e.data)).catch(error=>this.fail(error.message)).finally(()=>this.pendingMessages--);};this.ws.onerror=()=>this.fail('EdgeRTC connection failed.');this.ws.onclose=e=>{this.diagnostics?.note('rtc_websocket_close',{source:this.diagnosticKey,code:e?.code,clean:e?.wasClean});if(!this.closed)this.fail('EdgeRTC source disconnected.');};
  }
  fail(message,retryable=true){if(this.closed)return;this.diagnostics?.note('rtc_error',{source:this.diagnosticKey,message,retryable});this.options.onError?.(message,{retryable});this.close();}
  diagnosticState(){return {state:this.closed?'closed':this.playing?'playing':this.connectionState,codec:this._codec,width:this.width,height:this.height,
    first_frame_ms:this.firstFrameMS,frames:this.frames,dropped_frames:this.droppedFrames,source_fps:this.frameInterval?1000/this.frameInterval:null,
    decode_queue:this.video?.decodeQueueSize,render_queue:this.frameQueue.length,message_queue:this.pendingMessages,received_bytes:this.receivedBytes,
    last_packet_age_ms:this.lastPacketAt?performance.now()-this.lastPacketAt:null,websocket_outgoing_buffer_bytes:this.ws?.bufferedAmount,
    measured_end_to_end_latency_ms:null,audio_state:this.audio?.state||'muted_or_not_started'};}
  async message(data){
    if(this.closed)return;
    try{
      if(typeof data==='string'){
        const msg=JSON.parse(data);if(msg.type==='error'){this.fail(`EdgeRTC: ${msg.code}`);return;}if(msg.type!=='hello'||msg.protocol!=='edgertc/1')return;
        this.bootstrapTimestamp=Number.isFinite(msg.bootstrap_timestamp_us)?msg.bootstrap_timestamp_us:null;this.audioInfo=msg.audio;this.microphoneSupported=Boolean(msg.microphone?.supported);
        const configuration={codec:msg.video.codec,optimizeForLatency:true,hardwareAcceleration:'prefer-hardware'};
        let supported;
        try{supported=await VideoDecoder.isConfigSupported(configuration);}catch(_){supported={supported:false};}
        if(!supported.supported){configuration.hardwareAcceleration='no-preference';Object.assign(supported,await VideoDecoder.isConfigSupported(configuration));}
        if(this.closed)return;
        if(!supported.supported){const hevc=/^(hev1|hvc1)\./.test(msg.video.codec);this.fail(hevc?'EdgeRTC: this browser/device cannot decode this H.265 profile. Select H.264 in the camera recording profile, or use a device/browser with HEVC decoding. H.265 recording remains available.':'EdgeRTC: this browser cannot decode the camera H.264 profile.',false);return;}
        this.video=new VideoDecoder({output:frame=>this.render(frame),error:error=>this.fail('EdgeRTC video decoder: '+error.message)});
        this._codec=msg.video.codec;this.decoderConfiguration=configuration;this.videoCodecName=msg.video.codec_name||(/^(hev1|hvc1)\./.test(msg.video.codec)?'h265':'h264');this.video.configure(configuration);
        this.diagnostics?.note('rtc_decoder_configured',{source:this.diagnosticKey,codec:this._codec,requested_acceleration:configuration.hardwareAcceleration,audio_codec:msg.audio?.codec,audio_rate:msg.audio?.sampleRate,microphone_supported:this.microphoneSupported});
        this.connectionState='connected';this.options.onReady?.();
        if(this.options.microphone){if(!msg.microphone?.active)throw new Error('Camera microphone is unavailable.');await this.startMicrophone();}
        return;
      }
      const bytes=new Uint8Array(data);if(bytes.length<10)return;const timestamp=Number(new DataView(data).getBigInt64(2));const payload=bytes.subarray(10);
      if(bytes[0]===1&&this.video?.state==='configured'){
        const key=Boolean(bytes[1]&1);if(key)this.waitKey=false;if(this.waitKey)return;
        // A slow decoder cannot accumulate seconds of old frames.
        if(this.video.decodeQueueSize>(this.caughtUp?8:180)){this.clearFrames();this.video.reset();this.video.configure(this.decoderConfiguration);this.waitKey=!key;if(!key)return;}
        this.video.decode(new EncodedVideoChunk({type:key?'key':'delta',timestamp,data:payload}));
      }else if(bytes[0]===2&&!this.muted)this.decodeAudio(payload,timestamp);
    }catch(error){this.fail(error.message||'EdgeRTC decoding failed.');}
  }
  clearFrames(){
    if(this.renderRequest!=null)cancelAnimationFrame(this.renderRequest);
    this.renderRequest=null;for(const item of this.frameQueue)item.frame.close();this.frameQueue=[];this.lastScheduled=null;this.lastTimestamp=null;
  }
  render(frame){
    if(this.closed){frame.close();return;}
    if(this.bootstrapTimestamp!==null&&this.bootstrapTimestamp!==undefined&&frame.timestamp<this.bootstrapTimestamp){this.bootstrapFrames++;frame.close();return;}
    this.bootstrapTimestamp=null;
    const now=performance.now(),timestamp=Number(frame.timestamp);
    const interval=this.lastTimestamp==null?0:(timestamp-this.lastTimestamp)/1000;
    if(interval>0&&interval<1000)this.frameInterval=this.frameInterval==null?interval:this.frameInterval*.9+interval*.1;
    const due=interval>0&&interval<1000?Math.max(now,(this.lastScheduled??now)+interval):now;
    this.lastTimestamp=timestamp;this.lastScheduled=due;
    this.frameQueue.push({frame,due});
    // Normal 20/30 FPS bursts are paced by camera timestamps. Bound latency
    // when the tab/device falls behind; never synthesize or resize a frame.
    while(this.frameQueue.length>6||this.frameQueue.length>1&&this.frameQueue[0].due<now-120){this.frameQueue.shift().frame.close();this.droppedFrames++;}
    if(this.frameQueue.length&&this.frameQueue[this.frameQueue.length-1].due>now+120){
      const shift=this.frameQueue[this.frameQueue.length-1].due-now;
      for(const item of this.frameQueue)item.due-=shift;this.lastScheduled-=shift;
    }
    if(this.renderRequest==null)this.renderRequest=requestAnimationFrame(()=>this.drawFrame());
  }
  drawFrame(){
    this.renderRequest=null;if(this.closed)return;
    const now=performance.now();
    while(this.frameQueue.length>1&&this.frameQueue[0].due<now-120){this.frameQueue.shift().frame.close();this.droppedFrames++;}
    const item=this.frameQueue[0];
    if(item&&item.due<=now+1){
      this.frameQueue.shift();const current=item.frame;
      try{
        this.width=current.displayWidth;this.height=current.displayHeight;
        if(this.canvas.width!==this.width||this.canvas.height!==this.height){this.canvas.width=this.width;this.canvas.height=this.height;}
        this.context??=this.canvas.getContext('2d',{alpha:false,desynchronized:true});
        this.context.drawImage(current,0,0,this.width,this.height);
      }finally{current.close();}
      clearTimeout(this.connectTimer);this.playing=true;this.frames++;
      if((this.video?.decodeQueueSize??0)<3)this.caughtUp=true;
      if(this.firstFrameMS===null)this.firstFrameMS=now-this.started;
      if(this.diagnostics&&(!this.lastDiagnosticAt||now-this.lastDiagnosticAt>=1000)){
        const state={state:'playing',codec:this._codec,width:this.width,height:this.height,first_frame_ms:this.firstFrameMS,frames:this.frames,dropped_frames:this.droppedFrames,
          source_fps:this.frameInterval?1000/this.frameInterval:null,decode_queue:this.video?.decodeQueueSize,render_queue:this.frameQueue.length,message_queue:this.pendingMessages,
          received_bytes:this.receivedBytes,last_packet_age_ms:this.lastPacketAt?now-this.lastPacketAt:null,websocket_outgoing_buffer_bytes:this.ws?.bufferedAmount,
          measured_end_to_end_latency_ms:null,audio_state:this.audio?.state||'muted_or_not_started'};
        this.lastDiagnosticAt=now;this.diagnostics.source(this.diagnosticKey,state);if(this.frames===1)this.diagnostics.note('rtc_first_frame',{source:this.diagnosticKey,...state});
      }
      this.options.onFrame?.({width:this.width,height:this.height,firstFrameMS:this.firstFrameMS,frames:this.frames,sourceFPS:this.frameInterval?1000/this.frameInterval:0,droppedFrames:this.droppedFrames,bootstrapFrames:this.bootstrapFrames,queuedFrames:this.frameQueue.length});
    }
    if(this.frameQueue.length)this.renderRequest=requestAnimationFrame(()=>this.drawFrame());
  }
  ensureAudio(){if(!this.audio){this.audio=new AudioContext({latencyHint:'interactive'});this.gain=this.audio.createGain();this.gain.connect(this.audio.destination);}this.gain.gain.value=this.muted?0:this.volume??0.6;this.audio.resume().catch(()=>{});return this.audio;}
  setAudio(volume,muted){this.volume=volume;this.muted=muted;if(!muted||this.audio)this.ensureAudio();}
  decodeAudio(payload,timestamp){
    const info=this.audioInfo;if(!info||this.audioDisabled)return;const audio=this.ensureAudio();
    if(info.codec==='pcma'||info.codec==='pcmu'){
      const buffer=audio.createBuffer(1,payload.length,8000);const out=buffer.getChannelData(0);
      for(let i=0;i<payload.length;i++){let v=payload[i],sample;if(info.codec==='pcmu'){v=~v&255;sample=(((v&15)<<3)+132)<<((v>>4)&7);sample=(v&128)?132-sample:sample-132;}else{v^=85;sample=(v&15)<<4;const exponent=(v>>4)&7;sample+=exponent?264:8;if(exponent>1)sample<<=exponent-1;if(!(v&128))sample=-sample;}out[i]=sample/32768;}this.playAudio(buffer);return;
    }
    if(!window.AudioDecoder){if(!this.audioUnavailableLogged){this.audioUnavailableLogged=true;this.diagnostics?.note('rtc_audio_decoder_missing',{source:this.diagnosticKey,codec:info.codec});}return;}
    if(!this.audioDecoder){this.audioDecoder=new AudioDecoder({output:data=>{if(this.closed){data.close();return;}const buffer=audio.createBuffer(data.numberOfChannels,data.numberOfFrames,data.sampleRate);for(let i=0;i<data.numberOfChannels;i++)data.copyTo(buffer.getChannelData(i),{planeIndex:i,format:'f32-planar'});data.close();this.playAudio(buffer);},error:error=>{this.diagnostics?.note('rtc_audio_error',{source:this.diagnosticKey,message:error.message,codec:info.codec});if(this.audioDecoder?.state!=='closed')this.audioDecoder?.close();this.audioDecoder=null;this.audioDisabled=true;}});const config={codec:info.codec,sampleRate:info.sampleRate,numberOfChannels:info.channels};if(info.config)config.description=Uint8Array.from(atob(info.config),c=>c.charCodeAt(0));this.audioDecoder.configure(config);}
    if(this.audioDecoder.decodeQueueSize<12)this.audioDecoder.decode(new EncodedAudioChunk({type:'key',timestamp,data:payload}));
  }
  playAudio(buffer){if(this.closed||this.muted)return;const now=this.audio.currentTime;this.audioTime=Math.max(now+0.04,this.audioTime||0);if(this.audioTime>now+0.25)this.audioTime=now+0.04;const source=this.audio.createBufferSource();source.buffer=buffer;source.connect(this.gain);source.start(this.audioTime);this.audioTime+=buffer.duration;}
  async startMicrophone(){this.micTimer=setTimeout(()=>this.fail('Microphone initialization timed out.'),10000);for(const track of this.options.microphone.getTracks())track.addEventListener('ended',()=>this.fail('Microphone device disconnected.'),{once:true});const audio=this.ensureAudio();await audio.audioWorklet.addModule('/edge/edge-pcm-worklet.js?v=0.11.13');if(this.closed)return;this.micSource=audio.createMediaStreamSource(this.options.microphone);this.worklet=new AudioWorkletNode(audio,'edge-pcm');this.silence=audio.createGain();this.silence.gain.value=0;this.micSource.connect(this.worklet);this.worklet.connect(this.silence);this.silence.connect(audio.destination);this.worklet.port.onmessage=event=>{if(this.closed||this.ws.readyState!==WebSocket.OPEN)return;if(this.ws.bufferedAmount>6400){this.fail('Microphone connection is too slow.');return;}const packet=new Uint8Array(321);packet[0]=3;packet.set(new Uint8Array(event.data),1);this.ws.send(packet);};clearTimeout(this.micTimer);this.options.onMicrophone?.(true);}
  close(){if(this.closed)return;this.diagnostics?.unregisterSource(this.diagnosticKey);this.diagnostics?.source(this.diagnosticKey,{state:'closed',frames:this.frames,dropped_frames:this.droppedFrames,first_frame_ms:this.firstFrameMS});this.diagnostics?.note('rtc_client_closed',{source:this.diagnosticKey,frames:this.frames});this.closed=true;clearTimeout(this.connectTimer);clearTimeout(this.micTimer);this.options.microphone?.getTracks().forEach(track=>track.stop());this.playing=false;this.connectionState='closed';this.ws?.close();if(this.video?.state!=='closed')this.video?.close();if(this.audioDecoder?.state!=='closed')this.audioDecoder?.close();this.clearFrames();this.worklet?.port.close();this.micSource?.disconnect();this.worklet?.disconnect();this.silence?.disconnect();this.audio?.close().catch(()=>{});}
}
