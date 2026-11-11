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
    window.signalingOnly=signalingOnly;document.body.innerHTML='';document.body.style.margin='8px';document.body.style.background='#0d151e';document.documentElement.style.setProperty('--primary-text-color','#edf5f7');document.documentElement.style.setProperty('--card-background-color','#111d28');
    const canvas=document.createElement('canvas');canvas.width=320;canvas.height=180;
    const ctx=canvas.getContext('2d');let frame=0;
    const draw=setInterval(()=>{ctx.fillStyle=`hsl(${frame++%360} 60% 45%)`;ctx.fillRect(0,0,320,180);ctx.fillStyle='white';ctx.fillText(String(frame),20,30);},40);
    const audio=new AudioContext();const oscillator=audio.createOscillator();const sink=audio.createMediaStreamDestination();oscillator.connect(sink);oscillator.start();
    const videoStream=canvas.captureStream(25);const sessions=new Map();let seq=0;
    window.fixture={sessions,captures:[],calls:[],signals:[],mode:'accept',denied:false,pending:false,draw,audio,videoStream};
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,value:async constraints=>{
     fixture.calls.push(constraints);if(fixture.denied)throw new DOMException('Denied','NotAllowedError');
     await audio.resume();const stream=sink.stream.clone();fixture.captures.push(stream);
     if(fixture.pending)await new Promise(resolve=>fixture.resolvePermission=resolve);
     return stream;
    }});
    const hass={locale:{language:'pl'},states:{'camera.front':{state:'idle',attributes:{friendly_name:'Front door',edge_camera_id:'front',edge_stream_source:'edge_nvr',edge_record_profile:'main',edge_stream_entities:{nvr:'camera.front',main:'camera.front_high',sub:'camera.front_low',talk:'camera.front_talk'}}}},
     async callWS(msg){
      fixture.signals.push(msg);
      if(msg.type==='camera/webrtc/get_client_config'){if(fixture.mode==='missing')throw new Error('No WebRTC provider');return {configuration:{iceServers:[]}};}
      if(msg.type==='camera/webrtc/candidate'){const pc=sessions.get(msg.session_id)?.pc;if(pc&&pc.signalingState!=='closed')await pc.addIceCandidate(msg.candidate);return;}
      throw new Error('Unexpected WS call: '+msg.type);
     },connection:{async subscribeMessage(callback,msg){
      if(msg.type!=='camera/webrtc/offer')throw new Error('Unexpected subscription');
      const target=fixture.players.find(player=>player._pc&&!player._pc.remoteDescription&&player._activeEntity===msg.entity_id);const localPC=target._pc;
      const pc=new RTCPeerConnection({iceServers:[]});const id=String(++seq);const session={pc,received:[],entity:msg.entity_id,localPC,target};sessions.set(id,session);
      // HA acknowledges the subscription before provider negotiation completes.
      (async()=>{try {
      pc.ontrack=event=>session.received.push(event.track);
      await pc.setRemoteDescription({type:'offer',sdp:msg.offer});
      const at=pc.getTransceivers().find(t=>t.receiver.track.kind==='audio');
      const vt=pc.getTransceivers().find(t=>t.receiver.track.kind==='video');
      await at.sender.replaceTrack(sink.stream.getAudioTracks()[0]);at.direction=fixture.mode==='reject'?'sendonly':'sendrecv';
      await vt.sender.replaceTrack(videoStream.getVideoTracks()[0]);vt.direction='sendonly';
      pc.onicecandidate=event=>{if(event.candidate)callback({type:'candidate',candidate:event.candidate.toJSON()});};
      callback({type:'session',session_id:id});
      const answer=await pc.createAnswer();await pc.setLocalDescription(answer);await callback({type:'answer',answer:answer.sdp});
      if(signalingOnly&&target._pc===localPC){for(const t of target._remoteStream.getTracks())target._remoteStream.removeTrack(t);const stream=new MediaStream([...videoStream.getVideoTracks(),...sink.stream.getAudioTracks()].map(t=>t.clone()));target._pc.ontrack({streams:[stream],track:stream.getVideoTracks()[0]});}
      } catch(error) {session.closed=true;pc.close();if(target._pc===localPC)callback({type:'error',message:error.message});}})();
      session.unsubCreated=true;return ()=>{session.closed=true;pc.close();};
     }}};
    for(const id of ['camera.front_high','camera.front_low','camera.front_talk'])hass.states[id]={state:'idle',attributes:{friendly_name:id}};
    fixture.originalCallWS=hass.callWS;window.hass=hass;window.card=document.createElement('edge-live-player');
    card.setConfig({entity:'camera.front',name:'Front <safe>',reconnect:false});card.hass=hass;fixture.players=[card];document.body.append(card);
   },process.env.EDGE_WEBRTC_SIGNALING_ONLY==='1');
   const video=page.locator('edge-live-player video');const mic=page.locator('edge-live-player [data-action="mic"]');
   const live=async()=>{try{await page.waitForFunction(()=>(card._pc?.connectionState==='connected'||(signalingOnly&&card._pc?.remoteDescription))&&card._video.readyState>=2&&!card._video.paused);}catch(error){console.error(await page.evaluate(()=>({status:card._status.textContent,state:card._pc?.connectionState,ice:card._pc?.iceConnectionState,video:card._video.readyState,sessions:[...fixture.sessions.values()].map(s=>({state:s.pc.connectionState,signaling:s.pc.signalingState}))})));throw error;}};
   await live();assert.equal(await page.evaluate(()=>fixture.calls.length),0,'autoplay opened microphone');
   assert.equal(await page.locator('edge-live-player h2').textContent(),'Front <safe>');
   await page.evaluate(()=>{window.originalVideo=card._video;window.originalPC=card._pc;card.hass={...hass,states:{...hass.states}};});
   assert(await page.evaluate(()=>card._video===originalVideo&&card._pc===originalPC),'HA state update restarted player');
   await page.locator('edge-live-player [data-volume]').fill('0.35');assert.equal(await video.evaluate(v=>v.volume),0.35);assert.equal(await video.evaluate(v=>v.muted),false);
   await page.locator('edge-live-player [data-action="settings"]').click();
   await page.locator('edge-live-player [data-fit]').selectOption('cover');assert.equal(await video.evaluate(v=>v.style.objectFit),'cover');
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'card overflow');
   await page.locator('edge-live-player [data-quality]').selectOption('sub');await live();assert.equal(await page.evaluate(()=>card._activeEntity),'camera.front_low');
   await page.locator('edge-live-player [data-quality]').selectOption('nvr');await live();assert.equal(await page.evaluate(()=>card._activeEntity),'camera.front');
   await mic.click();await live();await page.waitForFunction(()=>card._micNegotiated===true);assert.equal(await page.evaluate(()=>card._activeEntity),'camera.front_talk');
   assert.equal(await mic.getAttribute('aria-pressed'),'true');assert.equal(await video.evaluate(v=>v.muted),true);
   if(process.env.EDGE_WEBRTC_SIGNALING_ONLY!=='1')await page.waitForFunction(async()=>{const latest=[...fixture.sessions.values()].at(-1);const stats=await latest.pc.getStats();return [...stats.values()].some(s=>s.type==='inbound-rtp'&&s.kind==='audio'&&s.bytesReceived>0);});
   await mic.click();await live();assert.equal(await page.evaluate(()=>card._activeEntity),'camera.front');assert(await page.evaluate(()=>fixture.captures.every(s=>s.getTracks().every(t=>t.readyState==='ended'))),'microphone not released');assert.equal(await mic.getAttribute('aria-pressed'),'false');assert.equal(await video.evaluate(v=>v.muted),false);
   await mic.click();await page.waitForFunction(()=>card._micNegotiated);await page.evaluate(()=>{Object.defineProperty(card._pc,'connectionState',{value:'disconnected',configurable:true});card._pc.onconnectionstatechange();});await live();assert.equal(await page.evaluate(()=>card._activeEntity),'camera.front');assert(await page.evaluate(()=>!card._micStream));
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
   assert(await page.evaluate(()=>!card._pc&&fixture.captures.at(-1).getTracks().every(t=>t.readyState==='ended')));assert(!(await page.locator('edge-live-player .status').textContent()).includes('secret'));
   await page.locator('edge-live-player [data-action="play"]').click();await live();
   // Live can be stopped explicitly without a HA state refresh restarting it.
   await page.locator('edge-live-player [data-action="play"]').click();await page.evaluate(()=>card.hass={...hass});assert.equal(await page.evaluate(()=>card._pc),null);
   await page.locator('edge-live-player [data-action="play"]').click();await live();
   await page.evaluate(()=>{card.remove();fixture.mode='missing';document.body.append(card);});await page.waitForFunction(()=>card._status.textContent.includes('WebRTC jest niedostępne'));assert.equal(await page.evaluate(()=>card._pc),null);
   // A deferred config response must not resurrect an unavailable camera.
   await page.evaluate(()=>{card.remove();fixture.mode='accept';hass.callWS=()=>new Promise(resolve=>fixture.resolveConfig=resolve);card.hass=hass;document.body.append(card);card._action('reconnect');});
   await page.waitForFunction(()=>typeof fixture.resolveConfig==='function');await page.evaluate(()=>{card.hass={...hass,states:{'camera.front':{state:'unavailable',attributes:{}}}};fixture.resolveConfig({configuration:{}});});
   assert.equal(await page.evaluate(()=>card._pc),null);
   const changed=await page.evaluate(()=>{
    const editor=customElements.get('edge-live-card').getConfigElement();editor.hass=hass;editor.setConfig({entity:'camera.front'});document.body.append(editor);
    let result;editor.addEventListener('config-changed',e=>result=e.detail.config);const input=editor.shadowRoot.querySelector('[data-config="microphone"]');editor.hass={...hass};if(editor.shadowRoot.querySelector('[data-config="microphone"]')!==input)throw new Error('Editor replaced inputs on state update');input.checked=false;input.dispatchEvent(new Event('change'));return result;
   });assert.equal(changed.type,'custom:edge-live-card');assert.equal(changed.microphone,false);
   // The public card accepts 1–4 cameras and suspends invisible players.
   await page.evaluate(()=>{
    card.remove();document.querySelector('edge-live-card-editor').style.display='none';hass.callWS=fixture.originalCallWS;fixture.mode='accept';
    for(const key of ['side','back','garage']){
     const id='camera.'+key;const streams={nvr:id,main:id+'_high',sub:id+'_low',talk:id+'_talk'};
     hass.states[id]={state:'idle',attributes:{friendly_name:key,edge_camera_id:key,edge_stream_role:'nvr',edge_stream_source:'edge_nvr',edge_stream_entities:streams}};
     for(const variant of [streams.main,streams.sub,streams.talk])hass.states[variant]={state:'idle',attributes:{friendly_name:variant}};
    }
    window.multi=document.createElement('edge-live-card');window.cameraIds=['camera.front','camera.side','camera.back','camera.garage'];
   });
    // Players outside the viewport release their connection (0.16.0), so make room for all four.
    await page.setViewportSize({width,height:6000});
   for(let count=1;count<=4;count++){
    await page.evaluate(count=>{multi.setConfig({cameras:cameraIds.slice(0,count),reconnect:false});multi.hass=hass;fixture.players=[...multi._players.values()];if(!multi.isConnected)document.body.append(multi);},count);
    await page.waitForFunction(count=>multi._players.size===count&&[...multi._players.values()].every(p=>p._video.readyState>=2&&!p._video.paused),count);
    assert.equal(await page.locator('edge-live-card edge-live-player').count(),count);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'multi-camera overflow');
   }
   await page.evaluate(()=>{fixture.originalPlayers=[...multi._players.values()];fixture.originalVideos=fixture.originalPlayers.map(p=>p._video);multi.hass={...hass};});
   assert(await page.evaluate(()=>[...multi._players.values()].every((p,i)=>p===fixture.originalPlayers[i]&&p._video===fixture.originalVideos[i])),'multi state update replaced players');
   await page.locator('edge-live-card [data-view]').selectOption('camera.side');
   assert(await page.evaluate(()=>[...multi._players.values()].filter(p=>p._pc).length===1),'hidden cameras kept receiving');
   await page.locator('edge-live-card [data-view]').selectOption('all');await page.waitForFunction(()=>[...multi._players.values()].every(p=>p._pc?.remoteDescription&&p._video.readyState>=2));
   // Only one microphone is active across the grid; each returns to NVR on stop.
   const allMics=page.locator('edge-live-card edge-live-player [data-action="mic"]');await allMics.nth(0).click();await page.waitForFunction(()=>[...multi._players.values()][0]._micNegotiated);
   await allMics.nth(1).click();await page.waitForFunction(()=>[...multi._players.values()][1]._micNegotiated);
   assert(await page.evaluate(()=>!([...multi._players.values()][0]._micStream)),'two microphones active');
   await allMics.nth(1).click();await page.waitForFunction(()=>[...multi._players.values()].every(p=>!p._micStream&&p._activeEntity===p._config.entity));
   if(process.env.EDGE_NVR_SCREENSHOT_DIR)await page.screenshot({path:`${process.env.EDGE_NVR_SCREENSHOT_DIR}/edge-live-${label}.png`,fullPage:true});
   const editorResult=await page.evaluate(()=>{const editor=document.querySelector('edge-live-card-editor');let config;editor.addEventListener('config-changed',e=>config=e.detail.config);const count=editor.shadowRoot.querySelector('[data-config="count"]');count.value='4';count.dispatchEvent(new Event('change'));return {count:config.cameras.length,fields:editor.shadowRoot.querySelectorAll('[data-config^="entity-"]').length};});assert.deepEqual(editorResult,{count:4,fields:4});
   await page.evaluate(()=>multi.remove());try{await page.waitForFunction(()=>[...fixture.sessions.values()].every(s=>s.closed));}catch(error){console.error(await page.evaluate(()=>[...fixture.sessions.values()].filter(s=>!s.closed).map(s=>({entity:s.entity,unsub:s.unsubCreated,local:s.localPC.connectionState,remote:s.pc.connectionState,generation:s.target._generation,owns:s.target._pc===s.localPC,hasUnsubscribe:Boolean(s.target._unsubscribe),starting:s.target._starting,connected:s.target.isConnected}))));throw error;}
   assert.equal(errors.length,0,errors.join('\n'));console.log(`PASS ${label}: ${process.env.EDGE_WEBRTC_SIGNALING_ONLY==='1'?'real SDP + synthetic local media (transport NOT tested)':'real WebRTC video/audio loopback'}, NVR/main/sub routing, direct talk cleanup, 1–4 cameras/focus, controls and editor`);
   await context.close();
  }
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
