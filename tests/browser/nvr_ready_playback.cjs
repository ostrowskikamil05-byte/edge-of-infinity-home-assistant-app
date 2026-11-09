// Live prepares saved copies through JSON only; actual composed WebKit pixels are checked.
const {chromium,webkit}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',index=wk?0:1,base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18093';
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:430,height:799},isMobile:true,hasTouch:true,userAgent:wk?'Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/HomeAssistant':'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/156 Mobile Safari/537.36'});
  const page=await context.newPage();page.setDefaultTimeout(30000);const errors=[],hoverRequests=[],prepared=new Set(),liveVideoRequests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(new URL(r.url()).pathname.includes('/recordings/')&&r.frame()===page.mainFrame())liveVideoRequests.push({url:r.url(),at:Date.now()});});page.on('response',r=>{if(new URL(r.url()).pathname.endsWith('/api/recording/prepare'))void r.json().then(data=>{if(data.ready)prepared.add(r.request().postDataJSON().key);}).catch(()=>{});});page.on('request',r=>{const u=new URL(r.url());if(u.pathname.includes('/recording-thumbs/')&&u.searchParams.has('at'))hoverRequests.push(r.url());});
  await page.goto(base);await page.waitForFunction(()=>recordingStatus[0]?.files?.length>0);
  const deadline=Date.now()+30000;while(prepared.size<2){assert(Date.now()<deadline,'server copies were not prepared');await page.waitForTimeout(50);}
  assert.equal(await page.evaluate(()=>recordingBuffer.entries.size),0,'Live downloaded recording bodies into RAM');
  assert.deepEqual(liveVideoRequests,[],'saved video competed with Live');
  assert(await page.evaluate(()=>[...document.querySelectorAll('video[data-recording-player]')].every(v=>!v.getAttribute('src'))),'Live opened an offscreen recording decoder');
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
  assert(await page.evaluate(()=>fixtureStoppedLive>0&&fixtureStopBeforeRecording),'NVR loaded before the Live decoder was explicitly stopped');
  const video=page.locator(`video[data-recording-player="${index}"]`),slider=page.locator(`[data-recording-playback-seek="${index}"]`);
  await page.locator(`[data-recording-toggle="${index}"]`).click();
  await page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`);return !v.paused&&v.currentTime>.2&&v.videoWidth>0;},index);
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
  if(wk)await video.evaluate(v=>{v.style.opacity='0';});
  const screenshot=await video.screenshot();
  const composed=await page.evaluate(async image=>{
   const img=new Image();img.src='data:image/png;base64,'+image;await img.decode();
   const c=document.createElement('canvas');c.width=160;c.height=90;const g=c.getContext('2d');g.drawImage(img,0,0,160,90);
   const data=g.getImageData(20,20,120,35).data;let colored=0;for(let i=0;i<data.length;i+=4)if(Math.max(...data.slice(i,i+3))-Math.min(...data.slice(i,i+3))>60)colored++;
   return colored;
  },screenshot.toString('base64'));
  assert(composed>500,'actual composed player is black despite decoded video pixels');
  if(wk)assert(await video.evaluate(v=>{const s=recordingSurface.diagnostic(v);return s.presentation==='canvas'&&s.canvas_width===v.videoWidth&&s.canvas_height===v.videoHeight&&s.canvas_frames>0;}),'WebKit did not display original-resolution canvas frames');
  if(process.env.EDGE_NVR_SCREENSHOT_DIR)await video.screenshot({path:`${process.env.EDGE_NVR_SCREENSHOT_DIR}/ready-${wk?'webkit':'chromium'}.png`});
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="home"]').click();
  assert(await video.evaluate(v=>!v.getAttribute('src')),'returning to Live kept a recording decoder');await page.waitForTimeout(500);assert(await page.evaluate(()=>!recordingBuffer.job),'Live resumed a saved-video body download');
  assert.deepEqual(errors,[]);console.log(`PASS ${wk?'webkit':'chromium'}: JSON-only server preparation on Live, actual composed recording pixels, no hidden recording decoder or hover thumbnails, held main-picture scrub and native frame stepping`);
  await page.unrouteAll({behavior:'ignoreErrors'});await context.close();
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
