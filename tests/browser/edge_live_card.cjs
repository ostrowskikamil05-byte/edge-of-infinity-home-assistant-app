// Real browser WebRTC loopback; the HA WebSocket signaling envelope is simulated.
// EDGE_WEBRTC_SIGNALING_ONLY=1 negotiates real SDP but injects local media tracks
// for rendering when managed browser policy blocks all direct WebRTC transport.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try {
  for(const [label,width,height] of [['desktop',1280,900],['tablet',820,1180],['phone',390,844],['small-phone',320,740]]){
   const context=await browser.newContext({viewport:{width,height}});const page=await context.newPage();page.setDefaultTimeout(20000);const errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088');
   // Clear the addon UI and its polling before installing a minimal HA dashboard host.
   await page.goto((process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088')+'/test-card');
   await page.addScriptTag({path:path.resolve('edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www/edge-live-card.js')});
   await page.evaluate(signalingOnly=>{
    window.signalingOnly=signalingOnly;document.body.innerHTML='';document.body.style.margin='8px';
    const canvas=document.createElement('canvas');canvas.width=320;canvas.height=180;
    const ctx=canvas.getContext('2d');let frame=0;
    const draw=setInterval(()=>{ctx.fillStyle=`hsl(${frame++%360} 60% 45%)`;ctx.fillRect(0,0,320,180);ctx.fillStyle='white';ctx.fillText(String(frame),20,30);},40);
    const audio=new AudioContext();const oscillator=audio.createOscillator();const sink=audio.createMediaStreamDestination();oscillator.connect(sink);oscillator.start();
    const videoStream=canvas.captureStream(25);const sessions=new Map();let seq=0;
    window.fixture={sessions,captures:[],calls:[],mode:'accept',denied:false,pending:false,draw,audio,videoStream};
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,value:async constraints=>{
     fixture.calls.push(constraints);if(fixture.denied)throw new DOMException('Denied','NotAllowedError');
     await audio.resume();const stream=sink.stream.clone();fixture.captures.push(stream);
     if(fixture.pending)await new Promise(resolve=>fixture.resolvePermission=resolve);
     return stream;
    }});
    const hass={locale:{language:'pl'},states:{'camera.front':{state:'idle',attributes:{friendly_name:'Front door',edge_camera_id:'front'}}},
     async callWS(msg){
      if(msg.type==='camera/webrtc/get_client_config'){if(fixture.mode==='missing')throw new Error('No WebRTC provider');return {configuration:{iceServers:[]}};}
      if(msg.type==='camera/webrtc/candidate'){const pc=sessions.get(msg.session_id)?.pc;if(pc&&pc.signalingState!=='closed')await pc.addIceCandidate(msg.candidate);return;}
      throw new Error('Unexpected WS call: '+msg.type);
     },connection:{async subscribeMessage(callback,msg){
      if(msg.type!=='camera/webrtc/offer')throw new Error('Unexpected subscription');
      const pc=new RTCPeerConnection({iceServers:[]});const id=String(++seq);const session={pc,received:[]};sessions.set(id,session);
      pc.ontrack=event=>session.received.push(event.track);
      await pc.setRemoteDescription({type:'offer',sdp:msg.offer});
      const at=pc.getTransceivers().find(t=>t.receiver.track.kind==='audio');
      const vt=pc.getTransceivers().find(t=>t.receiver.track.kind==='video');
      await at.sender.replaceTrack(sink.stream.getAudioTracks()[0]);at.direction=fixture.mode==='reject'?'sendonly':'sendrecv';
      await vt.sender.replaceTrack(videoStream.getVideoTracks()[0]);vt.direction='sendonly';
      pc.onicecandidate=event=>{if(event.candidate)callback({type:'candidate',candidate:event.candidate.toJSON()});};
      callback({type:'session',session_id:id});
      const answer=await pc.createAnswer();await pc.setLocalDescription(answer);await callback({type:'answer',answer:answer.sdp});
      if(signalingOnly){for(const t of card._remoteStream.getTracks())card._remoteStream.removeTrack(t);const stream=new MediaStream([...videoStream.getVideoTracks(),...sink.stream.getAudioTracks()].map(t=>t.clone()));card._pc.ontrack({streams:[stream],track:stream.getVideoTracks()[0]});}
      return ()=>{session.closed=true;pc.close();};
     }}};
    window.hass=hass;window.card=document.createElement('edge-live-card');
    card.setConfig({entity:'camera.front',name:'Front <safe>',reconnect:false});card.hass=hass;document.body.append(card);
   },process.env.EDGE_WEBRTC_SIGNALING_ONLY==='1');
   const video=page.locator('edge-live-card video');const mic=page.locator('edge-live-card [data-action="mic"]');
   const live=async()=>{try{await page.waitForFunction(()=>(card._pc?.connectionState==='connected'||(signalingOnly&&card._pc?.remoteDescription))&&card._video.readyState>=2&&!card._video.paused);}catch(error){console.error(await page.evaluate(()=>({status:card._status.textContent,state:card._pc?.connectionState,ice:card._pc?.iceConnectionState,video:card._video.readyState,sessions:[...fixture.sessions.values()].map(s=>({state:s.pc.connectionState,signaling:s.pc.signalingState}))})));throw error;}};
   await live();assert.equal(await page.evaluate(()=>fixture.calls.length),0,'autoplay opened microphone');
   assert.equal(await page.locator('edge-live-card h2').textContent(),'Front <safe>');
   await page.evaluate(()=>{window.originalVideo=card._video;window.originalPC=card._pc;card.hass={...hass,states:{...hass.states}};});
   assert(await page.evaluate(()=>card._video===originalVideo&&card._pc===originalPC),'HA state update restarted player');
   await page.locator('edge-live-card [data-volume]').fill('0.35');assert.equal(await video.evaluate(v=>v.volume),0.35);assert.equal(await video.evaluate(v=>v.muted),false);
   await page.locator('edge-live-card [data-action="settings"]').click();
   await page.locator('edge-live-card [data-fit]').selectOption('cover');assert.equal(await video.evaluate(v=>v.style.objectFit),'cover');
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'card overflow');
   await mic.click();await live();await page.waitForFunction(()=>card._micNegotiated===true);
   assert.equal(await mic.getAttribute('aria-pressed'),'true');assert.equal(await video.evaluate(v=>v.muted),true);
   if(process.env.EDGE_WEBRTC_SIGNALING_ONLY!=='1')await page.waitForFunction(async()=>{const latest=[...fixture.sessions.values()].at(-1);const stats=await latest.pc.getStats();return [...stats.values()].some(s=>s.type==='inbound-rtp'&&s.kind==='audio'&&s.bytesReceived>0);});
   await mic.click();assert(await page.evaluate(()=>fixture.captures.every(s=>s.getTracks().every(t=>t.readyState==='ended'))),'microphone not released');assert.equal(await mic.getAttribute('aria-pressed'),'false');assert.equal(await video.evaluate(v=>v.muted),false);
   await page.evaluate(()=>fixture.denied=true);await mic.click();await page.waitForFunction(()=>card._status.textContent.includes('Brak zgody'));assert.equal(await mic.getAttribute('aria-pressed'),'false');await live();
   await page.evaluate(()=>{fixture.denied=false;fixture.mode='reject';});await mic.click();await live();await page.waitForFunction(()=>card._status.textContent.includes('nie przyjęło'));
   assert.equal(await mic.getAttribute('aria-pressed'),'false');assert(await page.evaluate(()=>fixture.captures.at(-1).getTracks().every(t=>t.readyState==='ended')));
   // Cancelling/removing a card while a browser permission prompt is pending must release the late stream.
   await page.evaluate(()=>{fixture.mode='accept';fixture.pending=true;});await mic.click();await page.waitForFunction(()=>typeof fixture.resolvePermission==='function');
   await page.evaluate(()=>{card.remove();fixture.resolvePermission();});await page.waitForFunction(()=>fixture.captures.at(-1).getTracks().every(t=>t.readyState==='ended'));
   assert(await page.evaluate(()=>[...fixture.sessions.values()].every(s=>s.closed)),'session leaked on remove');
   await page.evaluate(()=>{fixture.pending=false;document.body.append(card);});await live();
   await mic.click();await page.waitForFunction(()=>card._micNegotiated);await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
   assert(await page.evaluate(()=>!card._pc&&fixture.captures.at(-1).getTracks().every(t=>t.readyState==='ended')),'hidden page kept microphone');
   const requests=await page.evaluate(()=>fixture.calls.length);await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});await live();assert.equal(await page.evaluate(()=>fixture.calls.length),requests,'visibility resume reopened microphone');
   // Provider failure releases capture and sanitizes credentials in the status.
   await mic.click();await page.waitForFunction(()=>card._micNegotiated);await page.evaluate(()=>card._event({type:'error',message:'rtsp://user:secret@192.0.2.3/source'},card._pc,card._generation));
   assert(await page.evaluate(()=>!card._pc&&fixture.captures.at(-1).getTracks().every(t=>t.readyState==='ended')));assert(!(await page.locator('edge-live-card .status').textContent()).includes('secret'));
   await page.locator('edge-live-card [data-action="play"]').click();await live();
   // Live can be stopped explicitly without a HA state refresh restarting it.
   await page.locator('edge-live-card [data-action="play"]').click();await page.evaluate(()=>card.hass={...hass});assert.equal(await page.evaluate(()=>card._pc),null);
   await page.locator('edge-live-card [data-action="play"]').click();await live();
   await page.evaluate(()=>{card.remove();fixture.mode='missing';document.body.append(card);});await page.waitForFunction(()=>card._status.textContent.includes('WebRTC jest niedostępne'));assert.equal(await page.evaluate(()=>card._pc),null);
   // A deferred config response must not resurrect an unavailable camera.
   await page.evaluate(()=>{card.remove();fixture.mode='accept';hass.callWS=()=>new Promise(resolve=>fixture.resolveConfig=resolve);card.hass=hass;document.body.append(card);card._action('reconnect');});
   await page.waitForFunction(()=>typeof fixture.resolveConfig==='function');await page.evaluate(()=>{card.hass={...hass,states:{'camera.front':{state:'unavailable',attributes:{}}}};fixture.resolveConfig({configuration:{}});});
   assert.equal(await page.evaluate(()=>card._pc),null);
   const changed=await page.evaluate(()=>{
    const editor=customElements.get('edge-live-card').getConfigElement();editor.hass=hass;editor.setConfig({entity:'camera.front'});document.body.append(editor);
    let result;editor.addEventListener('config-changed',e=>result=e.detail.config);const input=editor.shadowRoot.querySelector('[data-config="microphone"]');editor.hass={...hass};if(editor.shadowRoot.querySelector('[data-config="microphone"]')!==input)throw new Error('Editor replaced inputs on state update');input.checked=false;input.dispatchEvent(new Event('change'));return result;
   });assert.equal(changed.type,'custom:edge-live-card');assert.equal(changed.microphone,false);
   if(process.env.EDGE_NVR_SCREENSHOT_DIR)await page.screenshot({path:`${process.env.EDGE_NVR_SCREENSHOT_DIR}/edge-live-${label}.png`,fullPage:true});
   assert.equal(errors.length,0,errors.join('\n'));console.log(`PASS ${label}: ${process.env.EDGE_WEBRTC_SIGNALING_ONLY==='1'?'real SDP + synthetic local media (transport NOT tested)':'real WebRTC video/audio loopback'}, mic direction/rejection/denial/cleanup, controls, lifecycle and editor`);
   await context.close();
  }
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
