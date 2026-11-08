// Actual EdgeRTC/1 WS frames + WebCodecs + the real HA proxy implementation.
const {chromium}=require('playwright');const assert=require('node:assert/strict');const hevc=process.env.EDGE_RTC_TEST_CODEC==='h265';
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
try{for(const [name,width,height] of [['desktop',1440,1000],['tablet',820,1180],['phone',390,844],['small-phone',320,740]]){
 const context=await browser.newContext({viewport:{width,height}});const page=await context.newPage();page.setDefaultTimeout(20000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('websocket',socket=>{const url=new URL(socket.url());assert.equal(url.host,'127.0.0.1:18089','card bypassed the authenticated Home Assistant proxy');assert(url.pathname.startsWith('/api/edge/rtc/'),'card used a direct camera or alternate streaming path');});
 await page.goto('http://127.0.0.1:18089');await page.addScriptTag({url:'/edge/edge-live-card.js'});
 await page.evaluate(()=>{
  // Prove this path does not invoke either browser WebRTC or MSE.
  window.RTCPeerConnection=class{constructor(){throw new Error('WebRTC must not be used')}};window.MediaSource=class{constructor(){throw new Error('MSE must not be used')}};
  const audio=new AudioContext();const tone=audio.createOscillator();const dest=audio.createMediaStreamDestination();tone.connect(dest);tone.start();window.captures=[];window.permissions=0;
  navigator.mediaDevices.getUserMedia=async()=>{permissions++;await audio.resume();const stream=dest.stream.clone();captures.push(stream);return stream;};
  const ids=['camera.front','camera.side','camera.back','camera.garage'];window.hass={fetchWithAuth:(path,options)=>fetch(path,options),connection:{},locale:{language:'pl'},hassUrl:p=>new URL(p,location.href).href,states:Object.fromEntries(ids.map((id,index)=>[id,{state:'idle',attributes:{friendly_name:['Front','Side','Back','Garage'][index],edge_camera_id:String(index),edge_stream_source:'edge_rtc',edge_profiles:['nvr','main'],edge_stream_entities:{nvr:id,main:id}}}])),callWS:async msg=>{if(msg.type!=='auth/sign_path')throw new Error('Unexpected signaling API '+msg.type);return {path:msg.path};}};
  window.card=document.createElement('edge-live-card');card.setConfig({cameras:ids,reconnect:false});card.hass=hass;document.body.append(card);
 });
 const sourceStatus=await page.evaluate(()=>fetch('/test/status').then(r=>r.json()));
 const codec=sourceStatus.sources[0].video.codec;
 const supported=await page.evaluate(async codec=>(await VideoDecoder.isConfigSupported({codec,optimizeForLatency:true,hardwareAcceleration:'no-preference'})).supported,codec);
 if(hevc&&!supported){
  await page.waitForFunction(()=>[...card._players.values()].every(p=>p._hardFailed&&p._status.textContent.includes('H.265')));
  assert.equal(sourceStatus.sources[0].video.codec_name,'h265');assert.equal(sourceStatus.sources[0].connection_attempts,1);
  if(name==='desktop'){const recording=await page.evaluate(()=>fetch('/test/record').then(r=>r.json()));assert(recording.copy_video);assert(recording.files.some(s=>s.codec_name==='hevc'));}
  assert.equal(errors.length,0);console.log(`PASS ${name}: HEVC source/recording without transcoding, browser HEVC rejection explained, one upstream`);await context.close();continue;
 }
 try{await page.waitForFunction(()=>[...card._players.values()].every(p=>p._edgeRTC?.frames>=3));}catch(e){console.error(await page.evaluate(()=>({secure:isSecureContext,decoder:typeof VideoDecoder,players:[...card._players.values()].map(p=>({status:p._status.textContent,frames:p._edgeRTC?.frames,pc:p._pc?.connectionState})),errors:document.body.textContent})));throw e;}
 const metrics=await page.evaluate(()=>[...card._players.values()].map(p=>({firstFrameMS:p._edgeRTC.firstFrameMS,width:p._edgeRTC.width,height:p._edgeRTC.height})));
 assert(metrics.every(m=>m.width===640&&m.height===360));assert(metrics.every(m=>m.firstFrameMS<5000),'unreasonable first frame delay');assert.equal(await page.evaluate(()=>permissions),0);
 if(process.env.EDGE_RTC_TEST_FPS){
  const expected=Number(process.env.EDGE_RTC_TEST_FPS),before=await page.evaluate(()=>[...card._players.values()].map(p=>p._edgeRTC.frames));
  const started=Date.now();await page.waitForTimeout(2000);const elapsed=(Date.now()-started)/1000;
  const after=await page.evaluate(()=>[...card._players.values()].map(p=>({frames:p._edgeRTC.frames,fps:1000/p._edgeRTC.frameInterval,dropped:p._edgeRTC.droppedFrames})));
  after.forEach((row,i)=>{assert(Math.abs(row.fps-expected)<1,'camera timestamps changed');assert((row.frames-before[i])/elapsed>=expected*.8,'rendered FPS dropped below source-rate tolerance');});
  console.log(`Measured ${expected} FPS source: rendered ${after.map((row,i)=>((row.frames-before[i])/elapsed).toFixed(1)).join('/')} FPS`);
 }
 if(name==='desktop'){const recording=await page.evaluate(()=>fetch('/test/record').then(r=>r.json()));assert(recording.copy_video);assert(recording.files.length>0);assert(recording.files.every(s=>s.codec_name===(hevc?'hevc':'h264')&&s.width===640&&s.height===360));}
 if(process.env.EDGE_RTC_TEST_DETECTION){await page.waitForFunction(async()=>{const d=await fetch('/test/detection').then(r=>r.json());return d.cameras?.[0]?.local_status==='running'&&d.analysis_ms>0;});}
 const status=await page.evaluate(()=>fetch('/test/status').then(r=>r.json()));assert.equal(status.sources[0].upstream_connections,1);assert.equal(status.sources[0].connection_attempts,1);
 await page.evaluate(()=>{window.originalPlayers=[...card._players.values()];window.originalConnections=originalPlayers.map(p=>p._edgeRTC);card.hass={...hass};});assert(await page.evaluate(()=>[...card._players.values()].every((p,i)=>p._edgeRTC===originalConnections[i])),'HA update restarted native stream');
 const mic=page.locator('edge-live-card edge-live-player [data-action="mic"]').nth(0);await mic.click();await page.waitForFunction(()=>[...card._players.values()][0]._micNegotiated);
 await page.waitForFunction(async()=>{const status=await fetch('/test/status').then(r=>r.json());return status.sources[0].microphone_packets>3;});
 assert.equal(await mic.getAttribute('aria-pressed'),'true');await mic.click();await page.waitForFunction(()=>captures.every(s=>s.getTracks().every(t=>t.readyState==='ended')));
 const denied=await page.evaluate(()=>new Promise(resolve=>{const ws=new WebSocket('ws://127.0.0.1:18089/api/edge/rtc/camera.front?microphone=1&deny=1');ws.onopen=()=>{ws.close();resolve(false)};ws.onerror=()=>resolve(true);}));assert(denied,'read-only user was allowed microphone control');
 await page.locator('edge-live-card [data-view]').selectOption('camera.front');await page.waitForFunction(async()=>{const status=await fetch('/test/status').then(r=>r.json());return status.sources[0].viewers===1;});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'native card overflow');
 if(process.env.EDGE_NVR_SCREENSHOT_DIR)await page.screenshot({path:`${process.env.EDGE_NVR_SCREENSHOT_DIR}/edgertc-native-${name}.png`,fullPage:true});
 await page.evaluate(()=>card.remove());await page.waitForFunction(async()=>{const status=await fetch('/test/status').then(r=>r.json());return status.sources[0].viewers===0&&!status.sources[0].microphone_active;});
 if(name==='desktop'){const preview=await context.newPage();await preview.goto('http://127.0.0.1:18090/api/edge-rtc/player/front_main');await preview.waitForFunction(()=>document.querySelector('p').textContent.includes('640'));await preview.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});await page.waitForFunction(async()=>{const s=await fetch('/test/status').then(r=>r.json());return s.sources[0].viewers===0;});await preview.evaluate(()=>{document.querySelector('p').textContent='resuming';Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});await preview.waitForFunction(()=>document.querySelector('p').textContent.includes('640'));await preview.close();}
 const diagnosticDevice=await page.evaluate(()=>globalThis.__edgeDeviceReporter?.device_id);assert(diagnosticDevice);let deviceData;
 for(let i=0;i<60;i++){deviceData=await(await page.request.get('http://127.0.0.1:18089/test/devices?device='+diagnosticDevice)).json();if(deviceData.selected?.log.includes('rtc_first_frame'))break;await page.waitForTimeout(250);}
 assert(deviceData.selected?.log.includes('rtc_first_frame'),'live card telemetry was not persisted');
 const diagnosticsDenied=await page.request.post('http://127.0.0.1:18089/api/edge/client-diagnostics?deny=1',{data:{entity_id:'camera.front'}});assert.equal(diagnosticsDenied.status(),403);
 assert.equal(errors.length,0,errors.join('\n'));console.log(`PASS ${name}: actual ${hevc?'H265':'H264'} WebCodecs, 4 WS viewers + real browser PCM -> RTSP microphone, 1 upstream; first frames ${metrics.map(m=>Math.round(m.firstFrameMS)).join('/')} ms`);await context.close();
}}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
