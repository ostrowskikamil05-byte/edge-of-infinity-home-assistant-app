/* EdgeRTC archive: persistent socket, indexed seeks, bounded pull-based delivery. */
import {EdgeRTCClient,edgeMediaCoordinator,edgeMediaProblem,edgeRTCPlaybackSupport} from './edge-rtc-client.js?v=0.17.0';

export {edgeMediaProblem};

export class EdgeDVRClient extends EdgeRTCClient {
  constructor(options){super({...options,diagnosticSource:options.entity?options.entity+':archive'+(options.clip?':'+options.clip:''):options.diagnosticSource,audioPlaybackMode:'archive'});this.epoch=0;this.paused=true;this.speed=1;this.position=0;this.duration=0;this.archiveAudio=[];}
  async start(){try{
    const support=edgeRTCPlaybackSupport(this.options.hass?.locale?.language||this.options.language||navigator.language);
    if(!support.supported){const error=Object.assign(new Error(support.message),{code:support.code});this.close();throw error;}
    if(!this.options.url&&this.options.hass&&this.options.entity){
      const path='/api/edge/dashboard/archive/'+encodeURIComponent(this.options.entity)+(this.options.clip?'?clip='+encodeURIComponent(this.options.clip):'');
      const signed=await this.options.hass.callWS({type:'auth/sign_path',path,expires:3600});
      if(this.closed)return;
      this.options.url=this.options.hass.hassUrl?this.options.hass.hassUrl(signed.path):signed.path;
    }
    if(this.options.hass&&!this.options.diagnostics){
      try{const {getDeviceReporter}=await import('./edge-device-diagnostics.js?v=0.17.0');const hass=this.options.hass,entity=this.options.entity;
        this.options.diagnostics=getDeviceReporter({context:{surface:'home_assistant_dvr_card'},send:async payload=>{const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);try{return(await hass.fetchWithAuth('/api/edge/client-diagnostics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,entity_id:entity}),signal:controller.signal})).ok;}finally{clearTimeout(timer);}}});
      }catch(_){}
    }
    if(!this.closed)await super.start();
    }catch(error){this.close();throw error;}
  }
  seek(file,at,{paused=this.paused,rate=this.speed}={}){
    if(this.closed)return;
    this.epoch=(this.epoch+1)>>>0||1;this.request={file,at:Math.max(0,Number(at)||0)};this.paused=paused;this.speed=Math.min(4,Math.max(.5,Number(rate)||1));this.position=this.request.at;this.handlingFailure=false;clearTimeout(this.recoveryTimer);clearTimeout(this.yieldTimer);if(this.options.hass)edgeMediaCoordinator().claim(this);
    this.target=Math.round(this.request.at*1e6);this.seekStarted=performance.now();this.picture=false;this.playing=false;this.eof=false;this.networkEOF=false;this.ended=false;this.pulling=false;this.pendingPulls=0;this.queuedBatches=0;this.batchTimes=[];this.batchCount=0;this.sentEpoch=0;this.originWall=null;this.prerollFrames=0;this.seekBytes=0;this.audioDrainEpoch=null;this.archiveVideoPending=0;
    // A replaced seek must not wait behind old decoder/backpressure promises.
    // Their callbacks remain epoch-guarded and own no new request's counters.
    this.messageChain=Promise.resolve();this.pendingMessages=0;this.pendingBytes=0;
    clearTimeout(this.pullTimer);clearTimeout(this.seekTimer);this.bootstrapWake?.(false);this.clearFrames();this.clearAudio();this.archiveAudio=[];
    if(this.video?.state!=='closed')this.video?.close();this.video=null;
    if(this.audioDecoder?.state!=='closed')this.audioDecoder?.close();this.audioDecoder=null;
    this.diagnostics?.note('dvr_seek_requested',{source:this.diagnosticKey,epoch:this.epoch,file,seconds:this.request.at,paused,rate:this.speed});
    this.options.onWaiting?.(true);this.sendSeek();
  }
  sendSeek(){
    if(!this.socketReady||!this.request||this.ws?.readyState!==WebSocket.OPEN||this.sentEpoch===this.epoch)return;
    const count=this.archiveInitialBatch?Math.min(32,this.archiveBatchPackets):0;this.sentEpoch=this.epoch;
    if(count){this.pendingPulls++;this.pulling=true;this.batchTimes.push(performance.now());}
    this.ws.send(JSON.stringify({type:'seek',epoch:this.epoch,file:this.request.file,at:this.request.at,...(count?{count,...(this.archiveByteBudget?{bytes:1048576}:{})}:{})}));
    clearTimeout(this.seekTimer);this.seekTimer=setTimeout(()=>this.fail('EdgeRTC DVR: przekroczony czas dekodowania wybranej pozycji.'),15000);
  }
  play(){if(this.closed)return;this.seek(this.request.file,this.position,{paused:false});}
  pause(){if(this.closed)return;this.paused=true;this.playing=false;this.clearFrames();this.clearAudio();this.archiveAudio=[];clearTimeout(this.pullTimer);this.options.onState?.();this.releaseArchiveDecoder();}
  async fail(message,retryable=true,detail={}){
    if(this.closed||this.handlingFailure)return;
    this.handlingFailure=true;const failureEpoch=this.epoch;
    let code=detail.code||'media_error';
    if(code==='decoder_rejected_stream'&&this.options.hass&&!this.resourceRetry){
      this.resourceRetry=true;edgeMediaCoordinator().claim(this,true);this.epoch=(this.epoch+1)>>>0||1;
      this.diagnostics?.note('dvr_decoder_reclaim',{source:this.diagnosticKey,codec:this._codec});
      const file=this.request?.file,at=this.picture?this.position:this.request?.at,paused=this.paused;
      this.clearFrames();if(this.video?.state!=='closed')this.video?.close();this.video=null;
      this.recoveryTimer=setTimeout(()=>{if(!this.closed&&file){this.handlingFailure=false;this.seek(file,at,{paused});}},120);return;
    }
    if(this.options.hass&&['websocket_failed','source_disconnected','edge_archive_unavailable','upstream_unavailable'].includes(code)){
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),1500);
      try{
        const response=await this.options.hass.fetchWithAuth('/api/edge/dashboard/status/'+encodeURIComponent(this.options.entity),{signal:controller.signal});
        if(response.status===404)code='integration_restart_required';
        else if(response.status===401||response.status===403)code='authorization_failed';
        else if(!response.ok)code='upstream_unavailable';
        else{const status=await response.json();if(!status.archive)code='integration_restart_required';}
      }catch(_){}finally{clearTimeout(timer);}
    }
    if(this.closed||failureEpoch!==this.epoch)return;
    const problem={...detail,code,stage:this.connectionStage,codec:this._codec,epoch:this.epoch};
    super.fail(edgeMediaProblem(message,problem,this.language||this.options.hass?.locale?.language),retryable&&code!=='integration_restart_required',problem);
  }
  nativeFallback(code,message){this.diagnostics?.note('dvr_playback_unavailable',{code});this.fail(message,false,{code});}
  yieldMedia(){this.pause();this.epoch=(this.epoch+1)>>>0||1;clearTimeout(this.seekTimer);this.releaseArchiveDecoder(0);}
  releaseArchiveDecoder(delay=350){if(!this.options.hass)return;clearTimeout(this.yieldTimer);const release=()=>{if(this.closed||!this.paused||delay&&!this.picture)return;if(this.video?.state!=='closed')this.video?.close();this.video=null;if(this.audioDecoder?.state!=='closed')this.audioDecoder?.close();this.audioDecoder=null;this.archiveVideoPending=0;edgeMediaCoordinator().release(this);};if(delay)this.yieldTimer=setTimeout(release,delay);else release();}
  muteUnsupportedAudio(code){this.archiveAudio=[];this.setAudio(this.volume,true);this.diagnostics?.note('dvr_audio_unavailable',{code,video_continues:true});this.options.onAudioUnavailable?.(code);}
  setAudio(volume,muted){if(muted)this.archiveAudio=[];super.setAudio(volume,muted);if(!muted&&this.audioInfo)void this.checkArchiveAudio();}
  async checkArchiveAudio(){
    const info=this.audioInfo;if(this.closed||this.muted||!info||info.codec==='pcma'||info.codec==='pcmu')return true;
    const epoch=this.epoch;let supported=false;
    try{if(typeof AudioDecoder==='function'){const config={codec:info.codec,sampleRate:info.sampleRate,numberOfChannels:info.channels};if(info.config)config.description=Uint8Array.from(atob(info.config),c=>c.charCodeAt(0));supported=(await AudioDecoder.isConfigSupported(config)).supported;}}catch(_){}
    if(this.closed||epoch!==this.epoch)return false;
    if(!supported){this.muteUnsupportedAudio('audio_decoder_unavailable');return true;}return true;
  }
  setRate(rate){this.seek(this.request.file,this.position,{paused:this.paused,rate});}
  queueMessage(data){
    if(this.closed)return;
    let epoch;
    let acknowledgement;
    if(typeof data==='string'){let message;try{message=JSON.parse(data);}catch(_){this.fail('EdgeRTC DVR: nieprawidłowa odpowiedź serwera.');return;}if(message.type==='error'){void this.fail(message.message||`EdgeRTC DVR: ${message.code}`,true,{code:message.code||'upstream_unavailable'});return;}if(message.type==='archive_ready'){if(message.protocol!=='edgertc/archive/1'){this.fail('EdgeRTC DVR: niezgodny protokół.');return;}this.archiveBatchPackets=Math.min(64,Math.max(1,Number(message.batch_packets)||8));this.archiveInitialBatch=Boolean(message.initial_batch);this.archiveByteBudget=Boolean(message.byte_budget);this.archiveWindow=Math.min(2,Math.max(1,Number(message.pipeline_batches)||1));this.socketReady=true;this.sendSeek();return;}epoch=message.epoch;if(message.type==='batch')acknowledgement=message;}
    else{if(data.byteLength<14)return;epoch=new DataView(data).getUint32(10);}
    if(epoch!==this.epoch)return;
    if(acknowledgement){
      this.pendingPulls=Math.max(0,this.pendingPulls-1);this.pulling=this.pendingPulls>0;this.queuedBatches++;this.networkEOF||=Boolean(acknowledgement.end);this.batchCount++;
      const sent=this.batchTimes.shift();this.batchTransferMS=sent==null?null:Math.round(performance.now()-sent);this.batchBytes=acknowledgement.bytes??null;if(Number.isFinite(this.batchTransferMS)){const sample=Math.max(0,this.batchTransferMS-(acknowledgement.server_ms||0));this.archiveRTT=this.archiveRTT==null?sample:this.archiveRTT*.75+sample*.25;}this.batchReadMS=acknowledgement.read_ms??null;this.batchServerMS=acknowledgement.server_ms??null;
    }
    const size=data.byteLength??data.length;this.pendingMessages=(this.pendingMessages||0)+1;this.pendingBytes=(this.pendingBytes||0)+size;this.receivedBytes=(this.receivedBytes||0)+size;this.seekBytes+=size;this.lastPacketAt=performance.now();
    if(this.pendingMessages>192||this.pendingBytes>24*1024*1024){this.fail('EdgeRTC DVR: dekoder nie nadąża za danymi.');return;}
    this.messageChain=(this.messageChain||Promise.resolve()).then(()=>{if(!this.closed&&epoch===this.epoch)return this.archiveMessage(data,epoch);}).catch(error=>{if(!this.closed&&epoch===this.epoch)this.fail(error.message);}).finally(()=>{if(epoch===this.epoch){this.pendingMessages--;this.pendingBytes-=size;}});
    if(acknowledgement)this.pull();
  }
  async archiveMessage(data,epoch){
    if(typeof data==='string'){
      const message=JSON.parse(data);
      if(message.type==='error'){this.fail(`EdgeRTC DVR: ${message.code}.`,true,{code:message.code});return;}
      if(message.type==='archive_error'){this.fail(`EdgeRTC DVR: ${message.code}.`,false,{code:message.code});return;}
      if(message.type==='hello'){await this.configureArchive(message,epoch);return;}
      if(message.type==='batch'){
        this.queuedBatches=Math.max(0,this.queuedBatches-1);this.eof=Boolean(message.end);
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
      // Pause/preview resumes with a fresh indexed seek, so frames after its
      // first picture need neither GPU decoding nor additional memory.
      if(this.paused&&this.picture)return;
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
      if(this.archiveAudio.length>128){this.muteUnsupportedAudio('audio_queue_exceeded');return;}
      void this.drainArchiveAudio();
    }
  }
  async configureArchive(message,epoch){
    const config={codec:message.video.codec,optimizeForLatency:true,hardwareAcceleration:'prefer-hardware'};if(message.video.config)config.description=Uint8Array.from(atob(message.video.config),c=>c.charCodeAt(0));let supported;
    try{supported=await VideoDecoder.isConfigSupported(config);}catch(_){supported={supported:false};}
    if(!supported.supported){config.hardwareAcceleration='no-preference';supported=await VideoDecoder.isConfigSupported(config);}
    if(this.closed||epoch!==this.epoch)return;
    if(!supported.supported){this.nativeFallback('video_decoder_unavailable','EdgeRTC DVR: przeglądarka nie obsługuje profilu tego nagrania. Pobierz plik lub użyj przeglądarki z obsługą tego kodeka.');return;}
    this.connectionStage='decoding';this.target=message.target_us;this.duration=message.duration_us/1e6;this.frameDuration=1e6/Math.max(1,message.fps||25);this.audioInfo=message.audio;this._codec=message.video.codec;this.videoCodecName=message.video.codec_name;this.waitKey=true;
    try{this.video=new VideoDecoder({output:frame=>{if(this.closed||epoch!==this.epoch){frame.close();return;}this.archiveVideoPending=Math.max(0,this.archiveVideoPending-1);this.render(frame);},error:error=>{if(epoch===this.epoch)this.nativeFallback('decoder_rejected_stream','EdgeRTC DVR decoder: '+error.message+'. Pobierz plik, aby otworzyć go w innym odtwarzaczu.');}});this.video.configure(config);}catch(error){this.nativeFallback('decoder_rejected_stream','EdgeRTC DVR decoder: '+error.message);return;}this.connectionState='connected';
    this.diagnostics?.note('dvr_index_ready',{source:this.diagnosticKey,epoch,codec:this._codec,audio_codec:message.audio?.codec,seconds:message.target_us/1e6,keyframe_seconds:message.keyframe_us/1e6,keyframe_interval_seconds:message.keyframe_interval_seconds,index_hit:message.index_hit,index_ms:message.index_ms});
    this.options.onMetadata?.(message);if(await this.checkArchiveAudio())this.pull();
  }
  pull(){
    clearTimeout(this.pullTimer);
    if(this.closed||this.networkEOF||this.eof||this.paused&&this.picture||!this.video||this.ws?.readyState!==WebSocket.OPEN)return;
    if(this.pendingPulls+this.queuedBatches>=(this.archiveWindow||1))return;
    const ahead=this.frameQueue.length?(this.frameQueue.at(-1).frame.timestamp-this.position*1e6)/1e6/this.speed:0;
    const target=Math.min(1.1,Math.max(.4,2*(this.archiveRTT||0)/1000+.1));this.archiveLookahead=target;const frames=Math.min(20,Math.max(8,Math.ceil(target*1e6/this.frameDuration)));
    if(this.frameQueue.length>=frames||ahead>=target||this.video.decodeQueueSize>=8||this.picture&&this.archiveAudio.length>=((this.archiveRTT||0)>180?48:16)||this.pendingMessages>=96||this.pendingBytes>=12*1024*1024){this.pullTimer=setTimeout(()=>this.pull(),15);return;}
    const count=Math.min(this.archiveBatchPackets||8,this.picture&&(this.archiveRTT||0)<180?16:32);const bytes=(this.archiveRTT||0)>250?1048576:524288;
    this.pendingPulls++;this.pulling=true;this.batchTimes.push(performance.now());this.ws.send(JSON.stringify({type:'pull',epoch:this.epoch,count,...(this.archiveByteBudget?{bytes}:{})}));
  }
  render(frame){
    if(this.closed){frame.close();return;}
    if(frame.timestamp+this.frameDuration*.05<this.target){this.prerollFrames++;frame.close();return;}
    if(this.paused){if(!this.picture)this.present(frame);else frame.close();return;}
    if(this.frameQueue.length>=24){frame.close();this.fail('EdgeRTC DVR: przekroczony limit kolejki obrazu.');return;}
    if(!this.frameQueue.length&&this.originWall!=null){const due=this.originWall+(frame.timestamp-this.originPTS)/1000/this.speed;if(performance.now()-due>80){this.originWall+=performance.now()-due;this.archiveRebuffers=(this.archiveRebuffers||0)+1;}}
    this.frameQueue.push({frame});this.scheduleDraw();
  }
  scheduleDraw(){if(this.closed||this.paused||this.renderRequest!=null)return;this.renderRequest=requestAnimationFrame(()=>this.drawArchiveFrame());}
  drawArchiveFrame(){
    this.renderRequest=null;if(this.closed||this.paused)return;
    let item=this.frameQueue[0];const now=performance.now();
    if(this.speed>1&&this.originWall!=null){while(this.frameQueue.length>1&&this.originWall+(this.frameQueue[1].frame.timestamp-this.originPTS)/1000/this.speed<=now){this.frameQueue.shift().frame.close();this.droppedFrames++;}item=this.frameQueue[0];}
    if(item){
      if(this.originWall==null){this.originWall=now;this.originPTS=item.frame.timestamp;}
      const due=this.originWall+(item.frame.timestamp-this.originPTS)/1000/this.speed;
      if(due<=now+1){this.frameQueue.shift();this.present(item.frame);}
    }
    if(this.eof&&!this.frameQueue.length&&!this.archiveVideoPending&&!(this.video?.decodeQueueSize)&&this.picture){
      if(!this.ended){this.ended=true;this.playing=false;this.options.onEnded?.();if(this.ended){this.paused=true;this.releaseArchiveDecoder();}}return;
    }
    if(!this.frameQueue.length&&this.picture&&!this.eof)this.options.onWaiting?.(true);
    this.pull();if(this.frameQueue.length||this.archiveVideoPending||this.video?.decodeQueueSize)this.scheduleDraw();
  }
  present(frame){
    const first=!this.picture;this.width=frame.displayWidth;this.height=frame.displayHeight;
    if(this.canvas.width!==this.width||this.canvas.height!==this.height){this.canvas.width=this.width;this.canvas.height=this.height;}
    try{this.context??=this.canvas.getContext('2d',{alpha:false,desynchronized:true});if(!this.context)throw Error('canvas_unavailable');this.context.drawImage(frame,0,0,this.width,this.height);this.position=frame.timestamp/1e6;}catch(error){this.nativeFallback('canvas_unavailable','Nie udało się wyświetlić klatki EdgeRTC DVR. Pobierz nagranie, aby otworzyć je w innym odtwarzaczu.');return;}finally{frame.close();}
    this.connectionStage='playing';this.picture=true;this.frames++;this.playing=!this.paused;clearTimeout(this.connectTimer);clearTimeout(this.seekTimer);this.options.onWaiting?.(false);
    if(first){
      this.seekMS=performance.now()-this.seekStarted;this.firstFrameMS??=performance.now()-this.started;
      this.diagnostics?.note('dvr_seek_picture',{source:this.diagnosticKey,epoch:this.epoch,seconds:this.position,seek_ms:this.seekMS,preroll_frames:this.prerollFrames,received_bytes:this.seekBytes,width:this.width,height:this.height});
      if(!this.paused&&!this.muted)void this.drainArchiveAudio();
    }
    if(this.diagnostics&&(first||performance.now()-(this.lastDiagnosticAt||0)>1000)){this.lastDiagnosticAt=performance.now();this.diagnostics.source(this.diagnosticKey,this.diagnosticState());}
    if(this.paused)this.releaseArchiveDecoder();
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
          if(performance.now()-started>2000){this.muteUnsupportedAudio('audio_decoder_stalled');return;}
        }
        const packet=this.archiveAudio.shift();this.decodeAudio(packet.payload,packet.timestamp);
      }
    }finally{if(this.audioDrainEpoch===epoch)this.audioDrainEpoch=null;}
  }
  playPCM(channels,rate,timestamp){if(!this.paused)super.playPCM(channels,rate*this.speed,Math.round(this.target+(timestamp-this.target)/this.speed));}
  diagnosticState(){return {...super.diagnosticState(),transport:'edgertc/archive/1',epoch:this.epoch,paused:this.paused,seconds:this.position,seek_ms:this.seekMS,preroll_frames:this.prerollFrames,indexed_file:this.request?.file,archive_buffer_frames:this.frameQueue.length,archive_video_pending:this.archiveVideoPending,archive_audio_packets:this.archiveAudio.length,archive_audio_limit_ms:600,pull_pending:this.pulling,archive_network_rtt_ms:Math.round(this.archiveRTT||0),archive_lookahead_ms:Math.round((this.archiveLookahead||.4)*1000),archive_rebuffers:this.archiveRebuffers||0,archive_batch_count:this.batchCount,archive_batch_bytes:this.batchBytes??null,archive_byte_budget:this.archiveByteBudget?524288:null,archive_pending_pulls:this.pendingPulls,archive_queued_batches:this.queuedBatches,archive_batch_transfer_ms:this.batchTransferMS??null,archive_batch_read_ms:this.batchReadMS??null,archive_batch_server_ms:this.batchServerMS??null};}
  close(){clearTimeout(this.recoveryTimer);clearTimeout(this.yieldTimer);if(this.options.hass)edgeMediaCoordinator().release(this);clearTimeout(this.pullTimer);clearTimeout(this.seekTimer);this.archiveAudio=[];super.close();}
}
