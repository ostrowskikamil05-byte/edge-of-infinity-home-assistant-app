const {chromium,webkit}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const engine=process.env.EDGE_TEST_BROWSER||'chromium',wk=engine==='webkit';
 const browser=await (wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{
  for(const mobile of (wk?[true]:[false,true])){
   const context=await browser.newContext({viewport:{width:mobile?390:1440,height:mobile?844:1000},isMobile:mobile,hasTouch:mobile,userAgent:mobile?(wk?'Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148':'Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/142.0.0.0 Mobile Safari/537.36'):undefined});
   const page=await context.newPage();page.setDefaultTimeout(25000);const errors=[],requests=[];
   page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push({url:r.url(),method:r.method(),type:r.resourceType(),body:r.method()==='POST'?r.postData():null}));
   await page.route('**/api/edge-rtc/player/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><p>Live lifecycle fixture</p>'}));
   const base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18091';await page.goto(base);
   await page.locator('[data-camera-form]').first().waitFor({state:'attached'});
   await page.waitForTimeout(250);assert(!requests.some(r=>r.url.includes('/recording-thumbs/')),'hidden NVR generated thumbnail jobs during Live');
   if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
   const point=page.locator('[data-alert-point="1"]');await point.waitFor();await point.click();
   const watch=page.locator('[data-alert-watch][data-alert-index="1"]');await watch.waitFor();
   await page.evaluate(()=>window.retainedAlertVideo=document.querySelector('video[data-recording-player="1"]'));
   const statusCount=requests.filter(r=>r.url.includes('/api/recording/status')).length;
   await watch.click();
   await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player="1"]');return v===retainedAlertVideo&&!v.paused&&v.videoWidth>0&&recordingPlaybackPosition(v)>=9&&recordingPlaybackPosition(v)<12;});
   assert.equal(requests.filter(r=>r.url.includes('/api/recording/status')).length,statusCount,'same-day alert recreated/refetched the player');
   const video=page.locator('video[data-recording-player="1"]');
   // A fast direct seek can satisfy readyState/time before its first painted
   // picture. Wait for actual decoded pixels, not just metadata or an audio clock.
   await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player="1"]'),c=document.createElement('canvas');c.width=160;c.height=90;const g=c.getContext('2d');g.drawImage(v,0,0,160,90);return [...g.getImageData(0,0,160,90).data].filter((n,i)=>i%4!==3&&n>50).length>2000;},null,{timeout:3000});
   const picture=await video.evaluate(v=>{const c=document.createElement('canvas');c.width=160;c.height=90;const g=c.getContext('2d');g.drawImage(v,0,0,160,90);return [...g.getImageData(0,0,160,90).data].filter((n,i)=>i%4!==3&&n>50).length;});assert(picture>2000,'second-camera alert is black');
   await video.evaluate(v=>pauseRecording(v));
   assert(!requests.some(r=>r.url.includes('/api/recording/prepare')),'alert playback prepared a speculative recording');
   assert(!requests.some(r=>r.method==='GET'&&r.type==='fetch'&&r.url.includes('/recordings/')),'warmup fetched a video body');
   await page.evaluate(()=>selectRecordingAtOffset(1,9));await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player="1"]');return v.readyState>=2&&!v.seeking&&Math.abs(recordingPlaybackPosition(v)-9)<.2;});await video.evaluate(v=>{playRecording(v);v.currentTime=v.duration-.1;});
   await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player="1"]');return !v.paused&&v.dataset.recordingLoadedFile.includes('000014.mp4')&&v.currentTime>.1;});
   // Reproduce a browser pause during buffering, then new data arriving.
   await video.evaluate(v=>{v.dispatchEvent(new Event('waiting',{bubbles:true}));v.pause();});
   await page.waitForFunction(()=>document.querySelector('video[data-recording-player="1"]').paused);
   assert(await video.evaluate(v=>v.dataset.recordingWantsPlay==='true'));
   await video.evaluate(v=>v.dispatchEvent(new Event('canplay',{bubbles:true})));
   await page.waitForFunction(()=>!document.querySelector('video[data-recording-player="1"]').paused);
   // An otherwise healthy native pause must not erase Play intent either.
   await video.evaluate(v=>{v.dataset.recordingLoading='false';v.dataset.recordingBuffering='false';v.pause();});
   await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player="1"]');return !v.paused&&v.dataset.recordingWantsPlay==='true';});
   await video.evaluate(v=>{v.pause();pauseRecording(v);});await page.waitForTimeout(250);
   assert(await video.evaluate(v=>v.paused&&v.dataset.recordingWantsPlay==='false'),'queued recovery ignored an explicit Pause');
   await video.evaluate(v=>pauseRecording(v));await video.evaluate(v=>v.dispatchEvent(new Event('canplay',{bubbles:true})));
   assert(await video.evaluate(v=>v.paused&&v.dataset.recordingWantsPlay==='false'),'explicit Pause was ignored');
   // One real reload at the same time on a transient network error, then stop
   // retrying. The browser still decodes the real MP4 after that event.
   await video.evaluate(v=>playRecording(v));await page.waitForFunction(()=>!document.querySelector('video[data-recording-player="1"]').paused);
   const before=await video.evaluate(v=>v.currentTime);
   const networkError=()=>video.evaluate(v=>{Object.defineProperty(v,'error',{configurable:true,value:{code:2,message:'test network interruption'}});v.dispatchEvent(new Event('error'));delete v.error;});
   await networkError();await page.waitForFunction(before=>{const v=document.querySelector('video[data-recording-player="1"]');return v.dataset.recordingNetworkRetries==='1'&&!v.paused&&v.readyState>=2&&v.currentTime>=before;},before);
   await networkError();assert(await video.evaluate(v=>v.paused&&v.dataset.recordingWantsPlay==='false'));
   // Saved alert clips use a visible, hydrated native player too.
   await point.click();await page.locator('[data-alert-clip]').last().click();
   const clip=page.locator('dialog video');await page.waitForFunction(()=>{const v=document.querySelector('dialog video');return v&&!v.paused&&v.readyState>=2&&v.videoWidth>0&&v.currentTime>0;});
   assert.equal(new URL(await clip.getAttribute('src'),base).searchParams.get('compat'),'1');
   if(mobile)assert(await video.evaluate(v=>!v.getAttribute('src')),'dialog retained the inactive phone decoder');
   await page.locator('dialog button').click();assert.deepEqual(errors,[]);
   console.log(`PASS ${engine} ${mobile?'phone':'desktop'}: second-camera alert keeps its player, saved clip plays, no speculative preparation/media fetch, direct boundary and buffering resume, explicit Pause and bounded network recovery`);
   await page.unrouteAll({behavior:'ignoreErrors'});await page.close();await context.close();
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
