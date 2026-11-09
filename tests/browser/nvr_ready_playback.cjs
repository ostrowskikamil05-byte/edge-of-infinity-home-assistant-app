// Saved bytes may warm on Live, but no recording decoder runs until NVR opens.
const {chromium,webkit}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',index=wk?0:1,base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18093';
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:430,height:799},isMobile:true,hasTouch:true,userAgent:wk?'Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/HomeAssistant':'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/156 Mobile Safari/537.36'});
  const page=await context.newPage();page.setDefaultTimeout(30000);const errors=[],hoverRequests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{const u=new URL(r.url());if(u.pathname.includes('/recording-thumbs/')&&u.searchParams.has('at'))hoverRequests.push(r.url());});
  await page.route('**/api/edge-rtc/player/**',r=>r.fulfill({contentType:'text/html',body:'<p>Fixture live</p>'}));
  await page.goto(base);await page.waitForFunction(()=>recordingStatus[0]?.files?.length>0);
  await page.waitForTimeout(2400);
  assert.equal(await page.evaluate(()=>recordingBuffer.entries.size),0,'archive body competed with the first live picture');
  assert(await page.evaluate(()=>[...document.querySelectorAll('video[data-recording-player]')].every(v=>!v.getAttribute('src'))),'Live opened an offscreen recording decoder');
  // Use the authenticated same-origin frame notification the real player emits.
  await page.locator('iframe.live-frame').evaluateAll(frames=>frames.forEach(f=>f.contentWindow.eval("parent.postMessage({type:'edge_live_ready',path:location.pathname.split('/').pop()},location.origin)")));
  await page.waitForFunction(()=>recordingBuffer.entries.size===2&&!recordingBuffer.job);
  const warm=await page.evaluate(()=>({stats:recordingBuffer.diagnostic(),sources:[...recordingBuffer.entries.keys()]}));
  assert(warm.stats.bytes<=16*1024*1024&&warm.stats.files<=2);
  for(const url of warm.sources)await page.route(url,r=>r.abort('failed'));
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
  const video=page.locator(`video[data-recording-player="${index}"]`),slider=page.locator(`[data-recording-playback-seek="${index}"]`);
  await page.locator(`[data-recording-toggle="${index}"]`).click();
  await page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`);return v.src.startsWith('blob:')&&!v.paused&&v.currentTime>.2&&v.videoWidth>0;},index);
  assert.equal(await video.evaluate(v=>new URL(v.src).hash),'','native playback started through a fragment seek');
  await video.evaluate(v=>pauseRecording(v));
  await slider.hover();await page.waitForTimeout(250);
  assert.equal(await page.locator('[data-recording-scrub-preview]').count(),0);assert.deepEqual(hoverRequests,[]);
  const first=await video.evaluate(v=>{const c=document.createElement('canvas');c.width=160;c.height=90;c.getContext('2d').drawImage(v,0,0,160,90);return c.toDataURL();});
  // Keep the gesture held: the main picture changes without playing audio,
  // hover JPEGs, a second video element, or a connection to the camera.
  await slider.evaluate(s=>{s.value='6';s.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`),c=v.closest('[data-recording-wrap]').querySelector('[data-recording-transition]');return v.paused&&!v.seeking&&Math.abs(v.currentTime-6)<.08&&c.hidden;},index);
  const changed=await video.evaluate(v=>{const c=document.createElement('canvas');c.width=160;c.height=90;c.getContext('2d').drawImage(v,0,0,160,90);return c.toDataURL();});assert.notEqual(changed,first,'main image did not change during a held drag');
  await slider.evaluate(s=>s.dispatchEvent(new Event('change',{bubbles:true})));await page.waitForFunction(()=>recordingScrubs.size===0);
  await page.waitForFunction(index=>Number(document.querySelector(`video[data-recording-player="${index}"]`).dataset.recordingFps)>0,index);
  const clock=await video.evaluate(v=>v.currentTime);await page.locator(`[data-recording-frame="1"][data-record-index="${index}"]`).click();
  await page.waitForFunction(({index,clock})=>{const v=document.querySelector(`video[data-recording-player="${index}"]`);return !v.seeking&&Math.abs(v.currentTime-clock-1/Number(v.dataset.recordingFps))<.01;},{index,clock});
  assert(await video.evaluate(v=>v.paused));
  // Exercise native paused-frame fallback on browsers that suppress frame
  // callbacks. Keep the real HTMLVideoElement, MP4 decoder and seek events.
  await video.evaluate(v=>{recordingSurface.stop(v);v.requestVideoFrameCallback=undefined;});
  await slider.evaluate(s=>{s.value='12';s.dispatchEvent(new Event('input',{bubbles:true}));s.dispatchEvent(new Event('change',{bubbles:true}));});
  await page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`),s=recordingSurface.diagnostic(v);return v.paused&&!v.seeking&&Math.abs(v.currentTime-12)<.1&&s.native_picture_sampled&&!s.held_picture_visible;},index);
  const pixels=await video.evaluate(v=>{const c=document.createElement('canvas');c.width=160;c.height=90;const g=c.getContext('2d');g.drawImage(v,0,0,160,90);return [...g.getImageData(0,0,160,90).data].filter((n,i)=>i%4!==3&&n>50).length;});assert(pixels>2000,'decoded recording is black');
  if(process.env.EDGE_NVR_SCREENSHOT_DIR)await video.screenshot({path:`${process.env.EDGE_NVR_SCREENSHOT_DIR}/ready-${wk?'webkit':'chromium'}.png`});
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="home"]').click();
  assert(await video.evaluate(v=>!v.getAttribute('src')),'returning to Live kept a recording decoder');assert(await page.evaluate(()=>recordingBuffer.diagnostic().bytes>0),'returning to Live discarded the prepared recording bytes');
  assert.deepEqual(errors,[]);console.log(`PASS ${wk?'webkit':'chromium'}: live-first bounded byte warming, network-blocked first playback, no hidden recording decoder or hover thumbnails, held main-picture scrub and native frame stepping`);
  await page.unrouteAll({behavior:'ignoreErrors'});await context.close();
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
