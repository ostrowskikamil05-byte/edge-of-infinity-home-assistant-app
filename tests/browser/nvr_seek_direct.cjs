// Actual long-GOP files and native pictures. No video decoder/state mocks.
const {chromium,webkit}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18093';
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{for(const index of(wk?[0,1]:[1])){
  const context=await browser.newContext({viewport:{width:430,height:799},isMobile:true,hasTouch:true,userAgent:wk?'Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/HomeAssistant':'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/151 Mobile Safari/537.36'});
  const page=await context.newPage();page.setDefaultTimeout(30000);const errors=[],prepared=[],media=[];page.on('request',r=>{const path=new URL(r.url()).pathname;if(path.endsWith('/api/recording/prepare'))prepared.push(path);if(path.includes('/recordings/'))media.push(path);});page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/edge-rtc/player/**',r=>r.fulfill({contentType:'text/html',body:'<p>Generated camera</p>'}));
  await page.addInitScript(()=>localStorage.setItem('edge-nvr-next-buffer','true'));
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
  assert.equal(await page.evaluate(()=>typeof EdgeRecordingBuffer),'undefined');assert.equal(await page.locator('[data-recording-buffer-toggle]').count(),0);
  assert.deepEqual(prepared,[],'NVR prepared a speculative copy');assert(!media.some(path=>path.endsWith('000024.mp4')),'next file downloaded before being selected');
  await slider.evaluate(s=>{s.value='25';s.dispatchEvent(new Event('input',{bubbles:true}));s.dispatchEvent(new Event('change',{bubbles:true}));});
  await page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`);return !v.src.startsWith('blob:')&&!v.paused&&v.videoWidth>0&&v.currentTime>.1&&recordingSurface.diagnostic(v).picture_confirmed;},index);
  assert(media.some(path=>path.endsWith('000024.mp4')));assert.deepEqual(prepared,[]);
  const bright=await video.evaluate(v=>{const c=document.createElement('canvas');c.width=160;c.height=90;const g=c.getContext('2d');g.drawImage(v,0,0,160,90);return [...g.getImageData(0,0,160,90).data].filter((n,i)=>i%4!==3&&n>50).length;});assert(bright>2000,'native playback is black');
  await video.evaluate(v=>{pauseRecording(v);window.oldDirectPlayer=v;});
  await page.evaluate(index=>selectRecordingDay(index,'2026-07-27','test_decoder_cleanup'),index);
  await page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`);return v!==oldDirectPlayer&&v?.videoWidth>0&&!v.paused;},index);
  assert(await page.evaluate(()=>!oldDirectPlayer.getAttribute('src')&&recordingSurface.states.get(oldDirectPlayer).callback==null),'day replacement retained a decoder');
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="home"]').click();
  assert(await video.evaluate(v=>!v.getAttribute('src')));assert.deepEqual(errors,[]);
  console.log(`PASS ${wk?'webkit':'chromium'} camera ${index}: long-GOP seeks and native pictures, Play during drag, direct HTTP transitions, no prefetch/Blob cache and decoder cleanup`);
  await page.unrouteAll({behavior:'ignoreErrors'});await context.close();
 }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
