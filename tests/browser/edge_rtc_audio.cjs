// Actual Web Audio output and WebCodecs AAC, generated tones, no camera contact.
const {chromium,webkit}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http'),{spawnSync}=require('node:child_process');
function media(codec){
 const rate=codec==='aac'?16000:8000,format=codec==='aac'?'adts':codec==='pcma'?'alaw':'mulaw',encoder=codec==='aac'?'aac':codec==='pcma'?'pcm_alaw':'pcm_mulaw';
 const r=spawnSync('ffmpeg',['-v','error','-nostdin','-f','lavfi','-i',`sine=frequency=440:sample_rate=${rate}`,'-t','2','-c:a',encoder,'-threads:a','1','-f',format,'pipe:1'],{timeout:10000});assert.equal(r.status,0,r.stderr?.toString()||r.error?.message);
 const chunks=[];let description;
 if(codec==='aac'){
  for(let at=0;at+7<=r.stdout.length;){const h=r.stdout.subarray(at,at+7),size=((h[3]&3)<<11)|(h[4]<<3)|(h[5]>>5),header=h[1]&1?7:9;
   assert(size>=header&&at+size<=r.stdout.length);const asc=((h[2]>>6)+1)<<11|((h[2]>>2)&15)<<7|((h[2]&1)<<2|h[3]>>6)<<3;description=Buffer.from([asc>>8,asc&255]).toString('base64');chunks.push(r.stdout.subarray(at+header,at+size).toString('base64'));at+=size;}
 }else for(let at=0;at<r.stdout.length;at+=160)chunks.push(r.stdout.subarray(at,at+160).toString('base64'));
 return {codec:codec==='aac'?'mp4a.40.2':codec,sampleRate:rate,channels:1,config:description,duration:codec==='aac'?1024/rate:.02,chunks};
}
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',source=fs.readFileSync(path.join(__dirname,'../../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www/edge-rtc-client.js'),'utf8');
 const worklet=fs.readFileSync(path.join(__dirname,'../../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www/edge-pcm-worklet.js'),'utf8');
 const server=http.createServer((req,res)=>{const script=req.url.startsWith('/edge-pcm-worklet.js'),body=script?worklet:'<button id="audio-start">Start sound test</button>';res.writeHead(200,{'Content-Type':script?'text/javascript':'text/html','Content-Length':Buffer.byteLength(body),'Cache-Control':'no-store'});res.end(body);});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
 try{
  browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port+'/audio-fixture');
  await page.evaluate(async url=>{window.TestRTC=(await import(url)).EdgeRTCClient;},'data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  for(const codec of ['pcma','pcmu','aac']){
   const data=media(codec);
   const supported=codec!=='aac'||await page.evaluate(async info=>typeof AudioDecoder==='function'&&(await AudioDecoder.isConfigSupported({codec:info.codec,sampleRate:info.sampleRate,numberOfChannels:1,description:Uint8Array.from(atob(info.config),c=>c.charCodeAt(0))})).supported,data);
   if(!supported){console.log(`SKIP ${wk?'webkit':'chromium'} native live AAC: browser does not support WebCodecs AAC; G711 and native recording AAC remain tested`);continue;}
   await page.evaluate(info=>{
    document.querySelector('#audio-start').onclick=()=>{window.audioResult=(async()=>{
     const client=new TestRTC({canvas:{},audioWorkletURL:location.origin+'/edge-pcm-worklet.js'});client.audioInfo=info;client.setAudio(1,false);await client.audio.resume();
     const analyser=client.audio.createAnalyser();analyser.fftSize=1024;client.gain.disconnect();client.gain.connect(analyser);analyser.connect(client.audio.destination);let peak=0,sounding=0,inputPeak=0;const starts=[];
     const nativeSource=client.audio.createBufferSource.bind(client.audio);client.audio.createBufferSource=()=>{const source=nativeSource(),start=source.start.bind(source);source.start=at=>{starts.push([at,at+source.buffer.duration,client.audio.currentTime]);return start(at);};return source;};
     const enqueue=client.playPCM.bind(client);client.playPCM=(channels,rate,timestamp)=>{for(const channel of channels)inputPeak=Math.max(inputPeak,...channel.map(Math.abs));enqueue(channels,rate,timestamp);};
     const meter=setInterval(()=>{const samples=new Float32Array(1024);analyser.getFloatTimeDomainData(samples);const p=Math.max(...samples.map(Math.abs));peak=Math.max(peak,p);if(p>.04)sounding++;},10);
     try{
      // Compare with one continuous native source on this engine. Some
      // WebKit builds quantize analyser readback; absolute peaks alone cannot
      // distinguish that from mixed audio.
      const reference=client.audio.createBuffer(1,Math.round(info.sampleRate*.35),info.sampleRate),samples=reference.getChannelData(0);
      for(let i=0;i<samples.length;i++)samples[i]=.123*Math.sin(2*Math.PI*440*i/info.sampleRate);
      const referenceSource=client.audio.createBufferSource();referenceSource.buffer=reference;referenceSource.connect(client.gain);
      await new Promise(resolve=>{referenceSource.onended=resolve;referenceSource.start(client.audio.currentTime+.02);});
      const referencePeak=peak;peak=0;sounding=0;
      // Three 20 ms G711 packets or two 64 ms AAC packets arrive in bursts.
      const batch=info.codec==='mp4a.40.2'?2:3,started=performance.now();
      for(let at=0;at<info.chunks.length;at+=batch){for(let j=at;j<Math.min(at+batch,info.chunks.length);j++)client.decodeAudio(Uint8Array.from(atob(info.chunks[j]),c=>c.charCodeAt(0)),Math.round(j*info.duration*1e6));await new Promise(resolve=>setTimeout(resolve,Math.max(0,(at+batch)*info.duration*1000-(performance.now()-started))));}
      await new Promise(resolve=>setTimeout(resolve,300));
      for(let j=0;j<Math.min(info.chunks.length,info.codec==='mp4a.40.2'?12:50);j++)client.decodeAudio(Uint8Array.from(atob(info.chunks[j]),c=>c.charCodeAt(0)),4e6+Math.round(j*info.duration*1e6));
      await new Promise(resolve=>setTimeout(resolve,600));const state=client.diagnosticState();client.setAudio(1,true);await new Promise(resolve=>setTimeout(resolve,50));const muted=new Float32Array(1024);analyser.getFloatTimeDomainData(muted);const stopped=client.audioPending.length===0&&client.gain.gain.value===0&&muted.every(v=>Math.abs(v)<.005);client.close();return {peak,referencePeak,inputPeak,starts,sounding,stopped,state};
     }finally{clearInterval(meter);client.close();}
    })();};
   },data);
   await page.locator('#audio-start').click();const result=await page.evaluate(()=>audioResult);
   assert(result.peak>.04&&result.sounding>20&&result.referencePeak>.04,JSON.stringify(result));assert(result.peak<result.referencePeak*1.35+.01,'generated audio clipped/mixed: '+JSON.stringify(result));assert(result.stopped);assert(result.state.audio_queue_ms<=240);assert(result.state.audio_scheduled_ms<340);
   assert.equal(result.starts.length,1,'camera audio still created per-packet native sources');assert(result.state.audio_output_frames>20000);
   console.log(`PASS ${wk?'webkit':'chromium'} ${codec}: actual decoded tone, peak ${result.peak.toFixed(3)}, bounded jitter and mute cleanup`);
  }
  assert.deepEqual(errors,[]);
 }finally{await browser?.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exit(1);});
