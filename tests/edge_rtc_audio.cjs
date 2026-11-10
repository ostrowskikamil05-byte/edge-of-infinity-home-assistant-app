// Audio scheduling regression: real G711 reference samples, bounded bursts,
// monotonically scheduled native sources and explicit mute/close cleanup.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),vm=require('node:vm'),{spawnSync}=require('node:child_process');
(async()=>{
 const code=fs.readFileSync(path.join(__dirname,'../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www/edge-rtc-client.js'),'utf8');
 const {EdgeRTCClient}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
 const refs={},folder=fs.mkdtempSync(path.join(os.tmpdir(),'edge-g711-'));
 try{
  const file=path.join(folder,'bytes');fs.writeFileSync(file,Buffer.from(Array.from({length:256},(_,i)=>i)));
  for(const format of ['alaw','mulaw']){const ref=spawnSync('ffmpeg',['-v','error','-nostdin','-threads','1','-f',format,'-ar','8000','-ac','1','-i',file,'-threads','1','-f','f32le','pipe:1'],{timeout:5000});assert.equal(ref.status,0,ref.stderr?.toString()||ref.error?.message);refs[format]=ref.stdout;}
 }finally{fs.rmSync(folder,{recursive:true,force:true});}
 const classes=new Map(),stats=[];
 const context={sampleRate:48000,AudioWorkletProcessor:class{constructor(){this.port={postMessage:value=>stats.push(value)};}},registerProcessor:(name,klass)=>classes.set(name,klass)};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www/edge-pcm-worklet.js'),'utf8'),context);
 for(const [codec,format]of[['pcma','alaw'],['pcmu','mulaw']]){
  const client=new EdgeRTCClient({canvas:{}});client.audioInfo={codec};client.audio={resume:()=>Promise.resolve()};client.gain={gain:{}};let actual;
  client.playPCM=channels=>{actual=channels[0];};client.decodeAudio(Uint8Array.from({length:256},(_,i)=>i),0);
  for(let i=0;i<256;i++)assert(Math.abs(actual[i]-refs[format].readFloatLE(i*4))<1e-6,codec+' sample '+i);
 }
 const Processor=classes.get('edge-playback');
 function packet(p,rate,start,n,stereo=false){const data=new Float32Array(n);for(let i=0;i<n;i++)data[i]=.123*Math.sin(2*Math.PI*440*(start+i)/rate);p.port.onmessage({data:{type:'pcm',rate,timestamp:start/rate*1e6,channels:stereo?[data,data.slice()]:[data]}});}
 function quantum(p){const out=[new Float32Array(128),new Float32Array(128)];p.process([],[out]);assert.deepEqual(out[0],out[1]);return out[0].reduce((peak,sample)=>Math.max(peak,Math.abs(sample)),0);}
 for(const rate of [8000,16000,48000]){
  const p=new Processor();let delivered=0,peak=0;
  for(let tick=0;tick<Math.ceil(2.3*48000/128);tick++){
   const seconds=tick*128/48000;
   if(seconds<2&&delivered<=Math.floor(seconds/.06)*rate*.06){for(let i=0;i<3&&delivered<rate*2;i++){packet(p,rate,delivered,rate*.02,rate===48000);delivered+=rate*.02;}}
   peak=Math.max(peak,quantum(p));
  }
  assert(peak>.1&&peak<.16,'boundary peak from PCM resampling: '+peak);assert.equal(p.overruns,0);assert.equal(p.droppedFrames,0);assert(p.outputFrames>90000&&p.outputFrames<=96000,'samples duplicated or lost');
 }
 const p=new Processor();for(let i=0;i<100;i++)packet(p,8000,i*160,160);
 assert(p.overruns>0&&p.droppedFrames>0);assert((p.write-p.read)/8000<=.24);
 for(let i=0;i<130;i++)assert(quantum(p)<.16);
 p.port.onmessage({data:{type:'clear'}});assert.equal(p.write,0);assert.equal(p.read,0);assert.equal(quantum(p),0);
 // DVR video is allowed to queue 400 ms ahead. Preserve those AAC samples
 // rather than applying Live's 240 ms latest-frame policy to saved footage.
 for(const rate of[24000,48000,96000,192000]){
  const archive=new Processor({processorOptions:{mode:'archive'}});let delivered=0,nextBatch=0;
  for(let tick=0;tick<Math.ceil(2.1*48000/128);tick++){
   const seconds=tick*128/48000;
   if(seconds>=nextBatch){const until=Math.floor((seconds+.4)*rate/(rate*.02))*(rate*.02);while(delivered<until){packet(archive,rate,delivered,rate*.02,true);delivered+=rate*.02;}nextBatch+=.16;}
   quantum(archive);
  }
  assert.equal(archive.overruns,0,'DVR ahead-of-video audio was discarded');assert.equal(archive.underruns,0,'DVR burst delivery interrupted PCM');assert.equal(archive.droppedFrames,0);assert((archive.write-archive.read)/rate<=.6);assert(archive.outputFrames>90000);
  archive.port.onmessage({data:{type:'clear'}});assert.equal(archive.write,0);assert.equal(quantum(archive),0);
 }
 // Cellular delivery retains a complete 360 ms burst while the playout
 // reserve is being filled, including high-rate PCM. DVR ignores this policy.
 for(const rate of[8000,16000,48000,96000]){
  const mobile=new Processor({processorOptions:{mode:'live',network_target_ms:180}});let delivered=0,nextBatch=0;
  for(let tick=0;tick<Math.ceil(6*48000/128);tick++){
   const seconds=tick*128/48000;
   if(seconds>=.35&&mobile.networkTarget<280)mobile.port.onmessage({data:{type:'network',target_ms:280}});
   if(seconds>=.7&&mobile.networkTarget<400)mobile.port.onmessage({data:{type:'network',target_ms:400}});
   if(seconds>=nextBatch){const until=Math.round((nextBatch+.36)*rate);while(delivered<until){packet(mobile,rate,delivered,rate*.02);delivered+=rate*.02;}nextBatch+=.36;}
   quantum(mobile);
  }
  assert.equal(mobile.overruns,0,'cellular bursts overfilled PCM');assert.equal(mobile.underruns,0,'cellular bursts starved PCM');assert.equal(mobile.droppedFrames,0);assert(mobile.outputFrames>48000*5.2);assert((mobile.write-mobile.read)/rate<=1.04);
 }
 const unaffectedArchive=new Processor({processorOptions:{mode:'archive'}});unaffectedArchive.port.onmessage({data:{type:'network',target_ms:400}});assert.equal(unaffectedArchive.bufferSeconds,.6);assert.equal(unaffectedArchive.startSeconds,.12);
 const dvrCode=fs.readFileSync(path.join(__dirname,'../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www/edge-dvr-client.js'),'utf8').replace(/from '\.\/edge-rtc-client\.js\?[^']+'/,"from 'data:text/javascript;base64,"+Buffer.from(code).toString('base64')+"'");
 const {EdgeDVRClient}=await import('data:text/javascript;base64,'+Buffer.from(dvrCode).toString('base64'));
 const archiveClient=new EdgeDVRClient({canvas:{}}),decoded=[];archiveClient.picture=true;archiveClient.paused=false;archiveClient.muted=false;archiveClient.epoch=1;archiveClient.position=1;archiveClient.audioDecoder=new EventTarget();archiveClient.audioDecoder.decodeQueueSize=0;
 archiveClient.decodeAudio=(payload,timestamp)=>{assert(archiveClient.audioDecoder.decodeQueueSize<8);decoded.push(timestamp);archiveClient.audioDecoder.decodeQueueSize++;setTimeout(()=>{archiveClient.audioDecoder.decodeQueueSize--;archiveClient.audioDecoder.dispatchEvent(new Event('dequeue'));},2);};
 archiveClient.archiveAudio=Array.from({length:40},(_,i)=>({payload:new Uint8Array([i]),timestamp:i*21333}));await archiveClient.drainArchiveAudio();assert.deepEqual(decoded,Array.from({length:40},(_,i)=>i*21333));assert.equal(archiveClient.archiveAudio.length,0);
 // Encoded audio waits for picture time, and a replaced seek cancels that
 // wait instead of decoding the old epoch into the new sound queue.
 archiveClient.position=0;archiveClient.archiveAudio=[{payload:new Uint8Array([1]),timestamp:1000000}];
 const future=archiveClient.drainArchiveAudio();await new Promise(resolve=>setTimeout(resolve,25));assert.equal(decoded.length,40);
 archiveClient.position=.8;await future;assert.equal(decoded.at(-1),1000000);
 archiveClient.position=0;archiveClient.archiveAudio=[{payload:new Uint8Array([2]),timestamp:2000000}];
 const canceled=archiveClient.drainArchiveAudio();archiveClient.epoch++;archiveClient.archiveAudio=[];archiveClient.audioDrainEpoch=null;await canceled;assert.equal(decoded.length,41);
 // Closing the panel during module fetch is intentional cancellation. A
 // genuine failure while the player is open must still be reported.
 for(const closed of[true,false]){const c=new EdgeRTCClient({canvas:{}}),notes=[];let reject;c.diagnostics={note:event=>notes.push(event)};c.audioModule=()=>new Promise((resolve,fail)=>{reject=fail;});c.startAudioPlayback();await Promise.resolve();c.closed=closed;reject(Error('module download failed'));await c.audioPlaybackPromise;assert.equal(notes.includes('rtc_audio_error'),!closed);assert.equal(Boolean(c.audioDisabled),!closed);}
 console.log('PASS: G711 matches FFmpeg; continuous Live PCM; DVR 400 ms AAC bursts at 0.5/1/2/4x without sample loss, bounded queues and decoder backpressure');
})().catch(error=>{console.error(error);process.exit(1);});
