/* EdgeRTC archive: persistent socket, indexed seeks, bounded pull-based delivery. */
import {EdgeRTCClient} from './client.js?v=0.14.0';

export class EdgeDVRClient extends EdgeRTCClient {
  constructor(options){super({...options,audioPlaybackMode:'archive'});this.epoch=0;this.paused=true;this.speed=1;this.position=0;this.duration=0;this.archiveAudio=[];}
  async start(){await super.start();}
  seek(file,at,{paused=this.paused,rate=this.speed}={}){
    if(this.closed)return;
    this.epoch=(this.epoch+1)>>>0||1;this.request={file,at:Math.max(0,Number(at)||0)};this.paused=paused;this.speed=Math.min(2,Math.max(.5,Number(rate)||1));this.position=this.request.at;
    this.target=Math.round(this.request.at*1e6);this.seekStarted=performance.now();this.picture=false;this.playing=false;this.eof=false;this.ended=false;this.pulling=false;this.originWall=null;this.prerollFrames=0;this.seekBytes=0;this.audioDrainEpoch=null;this.archiveVideoPending=0;
    clearTimeout(this.pullTimer);clearTimeout(this.seekTimer);this.bootstrapWake?.(false);this.clearFrames();this.clearAudio();this.archiveAudio=[];
    if(this.video?.state!=='closed')this.video?.close();this.video=null;
    if(this.audioDecoder?.state!=='closed')this.audioDecoder?.close();this.audioDecoder=null;
    this.diagnostics?.note('dvr_seek_requested',{source:this.diagnosticKey,epoch:this.epoch,file,seconds:this.request.at,paused,rate:this.speed});
    this.options.onWaiting?.(true);this.sendSeek();
  }
  sendSeek(){
    if(!this.socketReady||!this.request||this.ws?.readyState!==WebSocket.OPEN)return;
    this.ws.send(JSON.stringify({type:'seek',epoch:this.epoch,file:this.request.file,at:this.request.at}));
    clearTimeout(this.seekTimer);this.seekTimer=setTimeout(()=>this.fail('EdgeRTC DVR: przekroczony czas dekodowania wybranej pozycji.'),15000);
  }
  play(){if(this.closed)return;this.seek(this.request.file,this.position,{paused:false});}
  pause(){if(this.closed)return;this.paused=true;this.playing=false;this.clearFrames();this.clearAudio();this.archiveAudio=[];clearTimeout(this.pullTimer);this.options.onState?.();}
  nativeFallback(code,message){const play=!this.paused,muted=this.muted,volume=this.volume;this.fail(message,false);this.options.onFallback?.({code,message,play,muted,volume});}
  setAudio(volume,muted){if(muted)this.archiveAudio=[];super.setAudio(volume,muted);if(!muted&&this.audioInfo)void this.checkArchiveAudio();}
  async checkArchiveAudio(){
    const info=this.audioInfo;if(this.closed||this.muted||!info||info.codec==='pcma'||info.codec==='pcmu')return true;
    const epoch=this.epoch;let supported=false;
    try{if(typeof AudioDecoder==='function'){const config={codec:info.codec,sampleRate:info.sampleRate,numberOfChannels:info.channels};if(info.config)config.description=Uint8Array.from(atob(info.config),c=>c.charCodeAt(0));supported=(await AudioDecoder.isConfigSupported(config)).supported;}}catch(_){}
    if(this.closed||epoch!==this.epoch)return false;
    if(!supported){this.nativeFallback('audio_decoder_unavailable','EdgeRTC DVR: ten dekoder dźwięku nie jest dostępny. Przełączam na odtwarzacz MP4.');return false;}return true;
  }
  setRate(rate){this.seek(this.request.file,this.position,{paused:this.paused,rate});}
  queueMessage(data){
    if(this.closed)return;
    let epoch;
    if(typeof data==='string'){let message;try{message=JSON.parse(data);}catch(_){this.fail('EdgeRTC DVR: nieprawidłowa odpowiedź serwera.');return;}if(message.type==='archive_ready'){if(message.protocol!=='edgertc/archive/1'){this.fail('EdgeRTC DVR: niezgodny protokół.');return;}this.archiveBatchPackets=Math.min(16,Math.max(1,Number(message.batch_packets)||8));this.socketReady=true;this.sendSeek();return;}epoch=message.epoch;}
    else{if(data.byteLength<14)return;epoch=new DataView(data).getUint32(10);}
    if(epoch!==this.epoch)return;
    const size=data.byteLength??data.length;this.pendingMessages=(this.pendingMessages||0)+1;this.pendingBytes=(this.pendingBytes||0)+size;this.receivedBytes=(this.receivedBytes||0)+size;this.seekBytes+=size;this.lastPacketAt=performance.now();
    if(this.pendingMessages>64||this.pendingBytes>24*1024*1024){this.fail('EdgeRTC DVR: dekoder nie nadąża za danymi.');return;}
    this.messageChain=(this.messageChain||Promise.resolve()).then(()=>{if(!this.closed&&epoch===this.epoch)return this.archiveMessage(data,epoch);}).catch(error=>{if(!this.closed&&epoch===this.epoch)this.fail(error.message);}).finally(()=>{this.pendingMessages--;this.pendingBytes-=size;});
  }
  async archiveMessage(data,epoch){
    if(typeof data==='string'){
      const message=JSON.parse(data);
      if(message.type==='archive_error'){if(message.code==='archive_requires_mp4')this.nativeFallback(message.code,'Ten plik wymaga odtwarzacza MP4.');else this.fail(`EdgeRTC DVR: ${message.code}.`,false);return;}
      if(message.type==='hello'){await this.configureArchive(message,epoch);return;}
      if(message.type==='batch'){
        this.pulling=false;this.eof=Boolean(message.end);
        // Flush only at EOF or for a paused preview. Play starts a fresh
        // keyframe request, as WebCodecs requires a key after flush().
        if(this.video?.state==='configured'&&(this.eof||this.paused&&!this.picture&&(this.lastDecodedTimestamp||0)>this.target+500000))await this.video.flush();
        if(epoch!==this.epoch||this.closed)return;
        this.pull();this.scheduleDraw();return;
      }
      return;
    }
    const bytes=new Uint8Array(data),timestamp=Number(new DataView(data).getBigInt64(2)),payload=bytes.subarray(14);
    if(bytes[0]===1&&this.video?.state==='configured'){
      const key=Boolean(bytes[1]&1);if(this.waitKey&&!key)return;this.waitKey=false;
      // decodeQueueSize excludes frames retained by the decoder for reordering.
      // Reserve capacity for every submitted picture until its output callback,
      // so a delayed burst cannot overflow the bounded presentation queue.
      while(!this.paused&&this.frameQueue.length+this.archiveVideoPending>=24){
        await new Promise(resolve=>setTimeout(resolve,15));
        if(epoch!==this.epoch||this.closed)return;
      }
      if(!await this.waitForBootstrapCapacity(timestamp)||epoch!==this.epoch||this.closed)return;
      this.lastDecodedTimestamp=timestamp;this.archiveVideoPending++;this.video.decode(new EncodedVideoChunk({type:key?'key':'delta',timestamp,data:payload}));
    }else if(bytes[0]===2&&!this.muted&&!this.paused&&timestamp>=this.target-20000){
      this.archiveAudio.push({payload:payload.slice(),timestamp});
      if(this.archiveAudio.length>128){this.nativeFallback('audio_queue_exceeded','EdgeRTC DVR: dekoder dźwięku nie nadąża. Przełączam na MP4.');return;}
      void this.drainArchiveAudio();
    }
  }
  async configureArchive(message,epoch){
    const config={codec:message.video.codec,optimizeForLatency:true,hardwareAcceleration:'prefer-hardware'};if(message.video.config)config.description=Uint8Array.from(atob(message.video.config),c=>c.charCodeAt(0));let supported;
    try{supported=await VideoDecoder.isConfigSupported(config);}catch(_){supported={supported:false};}
    if(!supported.supported){config.hardwareAcceleration='no-preference';supported=await VideoDecoder.isConfigSupported(config);}
    if(this.closed||epoch!==this.epoch)return;
    if(!supported.supported){this.nativeFallback('video_decoder_unavailable','EdgeRTC DVR: profil wideo wymaga odtwarzacza MP4 na tym urządzeniu.');return;}
    this.target=message.target_us;this.duration=message.duration_us/1e6;this.frameDuration=1e6/Math.max(1,message.fps||25);this.audioInfo=message.audio;this._codec=message.video.codec;this.videoCodecName=message.video.codec_name;this.waitKey=true;
    this.video=new VideoDecoder({output:frame=>{if(this.closed||epoch!==this.epoch){frame.close();return;}this.archiveVideoPending=Math.max(0,this.archiveVideoPending-1);this.render(frame);},error:error=>{if(epoch===this.epoch)this.nativeFallback('decoder_rejected_stream','EdgeRTC DVR decoder: '+error.message+'. Przełączam na MP4 dla tego urządzenia.');}});this.video.configure(config);this.connectionState='connected';
    this.diagnostics?.note('dvr_index_ready',{source:this.diagnosticKey,epoch,codec:this._codec,audio_codec:message.audio?.codec,seconds:message.target_us/1e6,keyframe_seconds:message.keyframe_us/1e6,keyframe_interval_seconds:message.keyframe_interval_seconds,index_hit:message.index_hit,index_ms:message.index_ms});
    this.options.onMetadata?.(message);if(await this.checkArchiveAudio())this.pull();
  }
  pull(){
    clearTimeout(this.pullTimer);
    if(this.closed||this.pulling||this.eof||this.paused&&this.picture||!this.video||this.ws?.readyState!==WebSocket.OPEN)return;
    const ahead=this.frameQueue.length?(this.frameQueue.at(-1).frame.timestamp-this.position*1e6)/1e6/this.speed:0;
    if(this.frameQueue.length>=8||ahead>=.4||this.video.decodeQueueSize>=8||this.picture&&this.archiveAudio.length>=16){this.pullTimer=setTimeout(()=>this.pull(),15);return;}
    this.pulling=true;this.ws.send(JSON.stringify({type:'pull',epoch:this.epoch,count:this.audioInfo?this.archiveBatchPackets||8:8}));
  }
  render(frame){
    if(this.closed){frame.close();return;}
    if(frame.timestamp+this.frameDuration*.05<this.target){this.prerollFrames++;frame.close();return;}
    if(this.paused){if(!this.picture)this.present(frame);else frame.close();return;}
    if(this.frameQueue.length>=24){frame.close();this.fail('EdgeRTC DVR: przekroczony limit kolejki obrazu.');return;}
    this.frameQueue.push({frame});this.scheduleDraw();
  }
  scheduleDraw(){if(this.closed||this.paused||this.renderRequest!=null)return;this.renderRequest=requestAnimationFrame(()=>this.drawArchiveFrame());}
  drawArchiveFrame(){
    this.renderRequest=null;if(this.closed||this.paused)return;
    const item=this.frameQueue[0],now=performance.now();
    if(item){
      if(this.originWall==null){this.originWall=now;this.originPTS=item.frame.timestamp;}
      const due=this.originWall+(item.frame.timestamp-this.originPTS)/1000/this.speed;
      if(due<=now+1){this.frameQueue.shift();this.present(item.frame);}
    }
    if(this.eof&&!this.frameQueue.length&&!(this.video?.decodeQueueSize)&&this.picture){
      if(!this.ended){this.ended=true;this.options.onEnded?.();}return;
    }
    if(!this.frameQueue.length&&this.picture&&!this.eof)this.options.onWaiting?.(true);
    this.pull();this.scheduleDraw();
  }
  present(frame){
    const first=!this.picture;this.width=frame.displayWidth;this.height=frame.displayHeight;
    if(this.canvas.width!==this.width||this.canvas.height!==this.height){this.canvas.width=this.width;this.canvas.height=this.height;}
    try{this.context??=this.canvas.getContext('2d',{alpha:false,desynchronized:true});if(!this.context)throw Error('canvas_unavailable');this.context.drawImage(frame,0,0,this.width,this.height);this.position=frame.timestamp/1e6;}catch(error){this.nativeFallback('canvas_unavailable','Nie udało się wyświetlić klatki EdgeRTC DVR. Przełączam na MP4.');return;}finally{frame.close();}
    this.picture=true;this.frames++;this.playing=!this.paused;clearTimeout(this.connectTimer);clearTimeout(this.seekTimer);this.options.onWaiting?.(false);
    if(first){
      this.seekMS=performance.now()-this.seekStarted;this.firstFrameMS??=performance.now()-this.started;
      this.diagnostics?.note('dvr_seek_picture',{source:this.diagnosticKey,epoch:this.epoch,seconds:this.position,seek_ms:this.seekMS,preroll_frames:this.prerollFrames,received_bytes:this.seekBytes,width:this.width,height:this.height});
      if(!this.paused&&!this.muted)void this.drainArchiveAudio();
    }
    this.options.onFrame?.({seconds:this.position,epoch:this.epoch,seekMS:this.seekMS,width:this.width,height:this.height,frames:this.frames});this.options.onState?.();
  }
  async drainArchiveAudio(){
    const epoch=this.epoch;if(this.audioDrainEpoch===epoch||!this.picture||this.paused||this.muted||this.closed)return;
    this.audioDrainEpoch=epoch;
    try{
      while(this.archiveAudio.length&&epoch===this.epoch&&!this.closed&&!this.paused&&!this.muted){
        // Keep encoded AAC until its video time approaches. Decoder reordering
        // can otherwise pull audio much further ahead than the picture queue.
        // Apply backpressure instead of throwing away already decoded samples.
        if((this.archiveAudio[0].timestamp-this.position*1e6)/1e6/this.speed>.3){await new Promise(resolve=>setTimeout(resolve,15));continue;}
        const started=performance.now();
        while(this.audioDecoder?.decodeQueueSize>=8){
          await new Promise(resolve=>{const decoder=this.audioDecoder;let timer;const done=()=>{clearTimeout(timer);decoder?.removeEventListener('dequeue',done);resolve();};decoder?.addEventListener('dequeue',done,{once:true});timer=setTimeout(done,25);});
          if(epoch!==this.epoch||this.closed||this.paused||this.muted)return;
          if(performance.now()-started>2000){this.nativeFallback('audio_decoder_stalled','EdgeRTC DVR: dekoder dźwięku zatrzymał się. Przełączam na MP4.');return;}
        }
        const packet=this.archiveAudio.shift();this.decodeAudio(packet.payload,packet.timestamp);
      }
    }finally{if(this.audioDrainEpoch===epoch)this.audioDrainEpoch=null;}
  }
  playPCM(channels,rate,timestamp){if(!this.paused)super.playPCM(channels,rate*this.speed,Math.round(this.target+(timestamp-this.target)/this.speed));}
  diagnosticState(){return {...super.diagnosticState(),transport:'edgertc/archive/1',epoch:this.epoch,paused:this.paused,seconds:this.position,seek_ms:this.seekMS,preroll_frames:this.prerollFrames,indexed_file:this.request?.file,archive_buffer_frames:this.frameQueue.length,archive_video_pending:this.archiveVideoPending,archive_audio_packets:this.archiveAudio.length,archive_audio_limit_ms:600,pull_pending:this.pulling};}
  close(){clearTimeout(this.pullTimer);clearTimeout(this.seekTimer);this.archiveAudio=[];super.close();}
}
