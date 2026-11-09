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
 // Closing the panel during module fetch is intentional cancellation. A
 // genuine failure while the player is open must still be reported.
 for(const closed of[true,false]){const c=new EdgeRTCClient({canvas:{}}),notes=[];let reject;c.diagnostics={note:event=>notes.push(event)};c.audioModule=()=>new Promise((resolve,fail)=>{reject=fail;});c.startAudioPlayback();await Promise.resolve();c.closed=closed;reject(Error('module download failed'));await c.audioPlaybackPromise;assert.equal(notes.includes('rtc_audio_error'),!closed);assert.equal(Boolean(c.audioDisabled),!closed);}
 console.log('PASS: PCMA/PCMU match FFmpeg; continuous mono/stereo 8/16/48 kHz PCM, bounded jitter, no boundary peaks, backlog/clear cleanup');
})().catch(error=>{console.error(error);process.exit(1);});
