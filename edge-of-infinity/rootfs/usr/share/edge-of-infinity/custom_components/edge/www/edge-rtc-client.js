/* EdgeRTC/1: H264 access units -> WebCodecs. No RTCPeerConnection, MP4 or MSE. */
export function edgeRTCPlaybackSupport(language=globalThis.navigator?.language||'en'){
  const pl=language.startsWith('pl');
  if(!globalThis.isSecureContext)return{supported:false,code:'insecure_context',message:pl?'EdgeRTC: otwórz Home Assistant przez HTTPS, np. adresem https://…ui.nabu.casa z Home Assistant Cloud. HTTP w sieci lokalnej blokuje WebCodecs.':'EdgeRTC: open Home Assistant over HTTPS, for example your https://…ui.nabu.casa Home Assistant Cloud address. Local-network HTTP blocks WebCodecs.'};
  if(typeof globalThis.VideoDecoder!=='function'||typeof globalThis.EncodedVideoChunk!=='function')return{supported:false,code:'webcodecs_unavailable',message:pl?'EdgeRTC: ta przeglądarka nie udostępnia dekodera WebCodecs. Otwórz adres HTTPS Home Assistanta w aktualnym Chrome lub Edge.':'EdgeRTC: this browser does not provide a WebCodecs decoder. Open the Home Assistant HTTPS address in an up-to-date Chrome or Edge browser.'};
  return{supported:true,code:'supported',message:''};
}

export class EdgeRTCClient {
  constructor(options){this.options=options;this.canvas=options.canvas;this.closed=false;this.playing=false;this.connectionState='connecting';this.waitKey=true;this.firstFrameMS=null;this.frames=0;this.started=performance.now();}
  async start(){
    const support=edgeRTCPlaybackSupport(this.options.language||this.options.hass?.locale?.language||this.options.hass?.language||globalThis.navigator?.language||'en');
    if(!support.supported){const error=new Error(support.message);error.code=support.code;error.retryable=false;this.close();throw error;}
    this.connectTimer=setTimeout(()=>this.fail('EdgeRTC camera timed out.'),15000);let url=this.options.url;
    if(!url){const path=`/api/edge/rtc/${this.options.entity}?microphone=${this.options.microphone?1:0}`;const signed=await this.options.hass.callWS({type:'auth/sign_path',path});if(this.closed)return;url=this.options.hass.hassUrl?this.options.hass.hassUrl(signed.path):new URL(signed.path,location.href).href;}
    if(this.closed)return;url=url.replace(/^http:/,'ws:').replace(/^https:/,'wss:');
    this.ws=new WebSocket(url);this.ws.binaryType='arraybuffer';this.ws.onmessage=e=>this.message(e.data);this.ws.onerror=()=>this.fail('EdgeRTC connection failed.');this.ws.onclose=()=>{if(!this.closed)this.fail('EdgeRTC source disconnected.');};
  }
  fail(message){if(this.closed)return;this.options.onError?.(message);this.close();}
  async message(data){
    if(this.closed)return;
    try{
      if(typeof data==='string'){
        const msg=JSON.parse(data);if(msg.type==='error'){this.fail(`EdgeRTC: ${msg.code}`);return;}if(msg.type!=='hello'||msg.protocol!=='edgertc/1')return;
        this.audioInfo=msg.audio;this.microphoneSupported=Boolean(msg.microphone?.supported);
        this.video=new VideoDecoder({output:frame=>this.render(frame),error:error=>this.fail('EdgeRTC video decoder: '+error.message)});
        this._codec=msg.video.codec;this.video.configure({codec:msg.video.codec,optimizeForLatency:true,hardwareAcceleration:'no-preference'});
        this.connectionState='connected';this.options.onReady?.();
        if(this.options.microphone){if(!msg.microphone?.active)throw new Error('Camera microphone is unavailable.');await this.startMicrophone();}
        return;
      }
      const bytes=new Uint8Array(data);if(bytes.length<10)return;const timestamp=Number(new DataView(data).getBigInt64(2));const payload=bytes.subarray(10);
      if(bytes[0]===1&&this.video?.state==='configured'){
        const key=Boolean(bytes[1]&1);if(key)this.waitKey=false;if(this.waitKey)return;
        // A slow decoder cannot accumulate seconds of old frames.
        if(this.video.decodeQueueSize>(this.caughtUp?8:180)){this.video.reset();this.video.configure({codec:this.videoCodec||this.options.codec||this._codec,optimizeForLatency:true,hardwareAcceleration:'no-preference'});this.waitKey=true;return;}
        this.video.decode(new EncodedVideoChunk({type:key?'key':'delta',timestamp,data:payload}));
      }else if(bytes[0]===2&&!this.muted)this.decodeAudio(payload,timestamp);
    }catch(error){this.fail(error.message||'EdgeRTC decoding failed.');}
  }
  render(frame){if(this.closed){frame.close();return;}this.latest?.close();this.latest=frame;if(this.renderPending)return;this.renderPending=true;requestAnimationFrame(()=>{this.renderPending=false;const current=this.latest;this.latest=null;if(!current)return;if(this.closed){current.close();return;}this.width=current.displayWidth;this.height=current.displayHeight;if(this.canvas.width!==this.width||this.canvas.height!==this.height){this.canvas.width=this.width;this.canvas.height=this.height;}this.canvas.getContext('2d',{alpha:false}).drawImage(current,0,0,this.width,this.height);current.close();clearTimeout(this.connectTimer);this.playing=true;this.frames++;if(this.video.decodeQueueSize<3)this.caughtUp=true;if(this.firstFrameMS===null)this.firstFrameMS=performance.now()-this.started;this.options.onFrame?.({width:this.width,height:this.height,firstFrameMS:this.firstFrameMS,frames:this.frames});});}
  ensureAudio(){if(!this.audio){this.audio=new AudioContext({latencyHint:'interactive'});this.gain=this.audio.createGain();this.gain.connect(this.audio.destination);}this.gain.gain.value=this.muted?0:this.volume??0.6;this.audio.resume().catch(()=>{});return this.audio;}
  setAudio(volume,muted){this.volume=volume;this.muted=muted;if(!muted||this.audio)this.ensureAudio();}
  decodeAudio(payload,timestamp){
    const info=this.audioInfo;if(!info||this.audioDisabled)return;const audio=this.ensureAudio();
    if(info.codec==='pcma'||info.codec==='pcmu'){
      const buffer=audio.createBuffer(1,payload.length,8000);const out=buffer.getChannelData(0);
      for(let i=0;i<payload.length;i++){let v=payload[i],sample;if(info.codec==='pcmu'){v=~v&255;sample=(((v&15)<<3)+132)<<((v>>4)&7);sample=(v&128)?132-sample:sample-132;}else{v^=85;sample=(v&15)<<4;const exponent=(v>>4)&7;sample+=exponent?264:8;if(exponent>1)sample<<=exponent-1;if(!(v&128))sample=-sample;}out[i]=sample/32768;}this.playAudio(buffer);return;
    }
    if(!window.AudioDecoder)return;
    if(!this.audioDecoder){this.audioDecoder=new AudioDecoder({output:data=>{if(this.closed){data.close();return;}const buffer=audio.createBuffer(data.numberOfChannels,data.numberOfFrames,data.sampleRate);for(let i=0;i<data.numberOfChannels;i++)data.copyTo(buffer.getChannelData(i),{planeIndex:i,format:'f32-planar'});data.close();this.playAudio(buffer);},error:()=>{if(this.audioDecoder?.state!=='closed')this.audioDecoder?.close();this.audioDecoder=null;this.audioDisabled=true;}});const config={codec:info.codec,sampleRate:info.sampleRate,numberOfChannels:info.channels};if(info.config)config.description=Uint8Array.from(atob(info.config),c=>c.charCodeAt(0));this.audioDecoder.configure(config);}
    if(this.audioDecoder.decodeQueueSize<12)this.audioDecoder.decode(new EncodedAudioChunk({type:'key',timestamp,data:payload}));
  }
  playAudio(buffer){if(this.closed||this.muted)return;const now=this.audio.currentTime;this.audioTime=Math.max(now+0.04,this.audioTime||0);if(this.audioTime>now+0.25)this.audioTime=now+0.04;const source=this.audio.createBufferSource();source.buffer=buffer;source.connect(this.gain);source.start(this.audioTime);this.audioTime+=buffer.duration;}
  async startMicrophone(){this.micTimer=setTimeout(()=>this.fail('Microphone initialization timed out.'),10000);for(const track of this.options.microphone.getTracks())track.addEventListener('ended',()=>this.fail('Microphone device disconnected.'),{once:true});const audio=this.ensureAudio();await audio.audioWorklet.addModule('/edge/edge-pcm-worklet.js?v=0.10.48');if(this.closed)return;this.micSource=audio.createMediaStreamSource(this.options.microphone);this.worklet=new AudioWorkletNode(audio,'edge-pcm');this.silence=audio.createGain();this.silence.gain.value=0;this.micSource.connect(this.worklet);this.worklet.connect(this.silence);this.silence.connect(audio.destination);this.worklet.port.onmessage=event=>{if(this.closed||this.ws.readyState!==WebSocket.OPEN)return;if(this.ws.bufferedAmount>6400){this.fail('Microphone connection is too slow.');return;}const packet=new Uint8Array(321);packet[0]=3;packet.set(new Uint8Array(event.data),1);this.ws.send(packet);};clearTimeout(this.micTimer);this.options.onMicrophone?.(true);}
  close(){if(this.closed)return;this.closed=true;clearTimeout(this.connectTimer);clearTimeout(this.micTimer);this.options.microphone?.getTracks().forEach(track=>track.stop());this.playing=false;this.connectionState='closed';this.ws?.close();if(this.video?.state!=='closed')this.video?.close();if(this.audioDecoder?.state!=='closed')this.audioDecoder?.close();this.latest?.close();this.latest=null;this.worklet?.port.close();this.micSource?.disconnect();this.worklet?.disconnect();this.silence?.disconnect();this.audio?.close().catch(()=>{});}
}
