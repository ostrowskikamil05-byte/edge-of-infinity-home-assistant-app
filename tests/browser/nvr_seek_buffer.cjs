// Actual long-GOP files and native pictures. No video decoder/state mocks.
const {chromium,webkit}=require('playwright');const assert=require('node:assert/strict');const crypto=require('node:crypto');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18093';
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{for(const index of(wk?[0,1]:[1])){
  const context=await browser.newContext({viewport:{width:430,height:799},isMobile:true,hasTouch:true,userAgent:wk?'Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/HomeAssistant':'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/151 Mobile Safari/537.36'});
  const page=await context.newPage();page.setDefaultTimeout(30000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/edge-rtc/player/**',r=>r.fulfill({contentType:'text/html',body:'<p>Generated camera</p>'}));
  await page.goto(base);await page.locator('[data-camera-form]').first().waitFor({state:'attached'});
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
  const video=page.locator(`video[data-recording-player="${index}"]`),slider=page.locator(`[data-recording-playback-seek="${index}"]`);
  await page.locator(`[data-recording-toggle="${index}"]`).click();
  await page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`);return !v.paused&&v.currentTime>.2&&v.videoWidth>0;},index);
  const rapid=await slider.evaluate(s=>{
   const v=document.querySelector(`video[data-recording-player="${s.dataset.recordingPlaybackSeek}"]`);
   s.value='18';s.dispatchEvent(new Event('input',{bubbles:true}));const pending=v.seeking;
   s.value='4';s.dispatchEvent(new Event('input',{bubbles:true}));s.dispatchEvent(new Event('change',{bubbles:true}));
   const c=v.closest('[data-recording-wrap]').querySelector('[data-recording-transition]');
   return {pending,time:v.currentTime,held:!c.hidden,epoch:recordingSurface.diagnostic(v).seek_epoch};
  });
  assert(rapid.pending,'test did not start a pending native seek');assert(Math.abs(rapid.time-4)<.1,'latest seek waited behind an obsolete same-file seek');assert(rapid.held,'last picture disappeared during the pending seek');
  await page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`),s=recordingSurface.diagnostic(v);return !v.paused&&v.currentTime>=4&&v.currentTime<8&&s.picture_confirmed&&!s.held_picture_visible;},index);
  // Play pressed during a drag from Pause must survive the release, before
  // any native play event has a chance to change the scrub state.
  await video.evaluate(v=>pauseRecording(v));
  await slider.evaluate(s=>{const v=document.querySelector(`video[data-recording-player="${s.dataset.recordingPlaybackSeek}"]`);s.value='10';s.dispatchEvent(new Event('input',{bubbles:true}));playRecording(v);s.dispatchEvent(new Event('change',{bubbles:true}));});
  await page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`);return !v.paused&&v.currentTime>=10&&v.currentTime<14&&recordingSurface.diagnostic(v).picture_confirmed;},index);
  await page.waitForFunction(index=>[...recordingBuffer.entries].some(([url])=>url.includes(index===0?'/front_0/':'/back_1/')&&url.includes('000024.mp4')),index);
  const cached=await page.evaluate(async index=>{
   const pair=[...recordingBuffer.entries].find(([url])=>url.includes(index===0?'/front_0/':'/back_1/')&&url.includes('000024.mp4'));
   const hash=await crypto.subtle.digest('SHA-256',await pair[1].blob.arrayBuffer());
   return {url:pair[0],hash:[...new Uint8Array(hash)].map(n=>n.toString(16).padStart(2,'0')).join(''),stats:recordingBuffer.diagnostic()};
  },index);
  const original=await page.request.get(cached.url);assert.equal(crypto.createHash('sha256').update(await original.body()).digest('hex'),cached.hash,'buffer changed original served video/audio bytes');
  assert(cached.stats.files<=2&&cached.stats.bytes<=16*1024*1024);
  await page.route('**/recordings/**000024.mp4*',r=>r.abort('failed'));
  await slider.evaluate(s=>{s.value='25';s.dispatchEvent(new Event('input',{bubbles:true}));s.dispatchEvent(new Event('change',{bubbles:true}));});
  await page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`);return v.src.startsWith('blob:')&&!v.paused&&v.videoWidth>0&&v.currentTime>.1&&recordingSurface.diagnostic(v).picture_confirmed;},index);
  const bright=await video.evaluate(v=>{const c=document.createElement('canvas');c.width=160;c.height=90;const g=c.getContext('2d');g.drawImage(v,0,0,160,90);return [...g.getImageData(0,0,160,90).data].filter((n,i)=>i%4!==3&&n>50).length;});assert(bright>2000,'cached playback is black');
  if(!await page.locator(`[data-recording-buffer-toggle]`).nth(index).isVisible())await page.locator(`[data-recording-more="${index}"]`).click();
  await page.locator('[data-recording-buffer-toggle]').nth(index).uncheck();
  assert(await video.evaluate(v=>!v.paused&&v.src.startsWith('blob:')),'disabling future warmup interrupted the active memory file');
  assert.equal(await page.evaluate(()=>recordingBuffer.enabled),false);
  await video.evaluate(v=>pauseRecording(v));assert(await video.evaluate(v=>v.paused&&v.dataset.recordingWantsPlay==='false'));
  await video.evaluate(v=>window.oldBufferedPlayer=v);
  await page.evaluate(index=>selectRecordingDay(index,'2026-07-27','test_decoder_cleanup'),index);
  await page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`);return v!==oldBufferedPlayer&&v?.videoWidth>0&&!v.paused;},index);
  assert(await page.evaluate(()=>!oldBufferedPlayer.getAttribute('src')&&!recordingBuffer.leases.has(oldBufferedPlayer)&&recordingSurface.states.get(oldBufferedPlayer).callback==null),'day replacement retained a decoder or memory URL');
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="home"]').click();
  assert.equal(await page.evaluate(()=>recordingBuffer.diagnostic().bytes),0);assert(await video.evaluate(v=>!v.getAttribute('src')));assert.deepEqual(errors,[]);
  await page.reload();await page.locator('[data-camera-form]').first().waitFor({state:'attached'});assert.equal(await page.evaluate(()=>recordingBuffer.enabled),false,'per-device buffer preference did not persist');
  const limits=await page.evaluate(async()=>{
   const notes=[],buffer=new EdgeRecordingBuffer((event,data)=>notes.push({event,...data})),owner=()=>({isConnected:true,getAttribute:()=>true,dataset:{recordingPlayer:'limits'}}),url=location.origin+'/recordings/front_0/20260728-000024.mp4';
   const large=await buffer.prime(owner(),url,9*1024*1024);
   for(let i=0;i<2;i++)buffer.entries.set(url+'?active='+i,{blob:new Blob([new Uint8Array(1024)]),users:new Set([owner()]),at:Date.now()});
   const third=await buffer.prime(owner(),url+'?third=1',1024);
   return {large,third,files:buffer.entries.size,job:Boolean(buffer.job),reasons:notes.map(n=>n.reason)};
  });
  assert(!limits.large&&!limits.third&&!limits.job&&limits.files===2);assert(limits.reasons.includes('file_size')&&limits.reasons.includes('memory_budget'));
  console.log(`PASS ${wk?'webkit':'chromium'} camera ${index}: actual 24-second GOP, pending seek superseded, held/confirmed pixels, Play during drag, byte-identical bounded RAM buffer and network-independent transition`);
  await page.unrouteAll({behavior:'ignoreErrors'});await context.close();
 }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
