const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  for(const [width,height] of [[1440,1000],[820,1180],[390,844],[320,740]]){
   const context=await browser.newContext({viewport:{width,height},hasTouch:width<1000,isMobile:width<1000});
   const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088');
   await page.locator('[name="camera-0-name"]').waitFor({state:'attached'});
   if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();
   await page.locator('nav [data-page-target="nvr"]').click();
   await page.waitForFunction(()=>document.querySelector('[data-nvr-day]')?.dataset.nvrDay==='2026-07-28');
   let held=null;
   await page.route('**/api/recording/status*',route=>{if(!held){held=route;return;}return route.continue();});
   await page.evaluate(()=>{void loadRecordingStatus({reason:'timer'});});
   await page.waitForFunction(()=>nvrLoading);
   await page.getByRole('button',{name:'Previous day',exact:true}).click();
   await page.waitForFunction(()=>document.querySelector('[data-nvr-day]')?.dataset.nvrDay==='2026-07-27',null,{timeout:8000});
   await page.waitForFunction(()=>document.querySelector('video')?.dataset.recordingFileUrl.includes('20260727'));
   try{await held.abort();}catch(_){}
   await page.unroute('**/api/recording/status*');
   await page.locator('[data-recording-day="2026-07-28"]').click();
   await page.locator('[data-recording-day="2026-07-27"]').click();
   await page.locator('[data-recording-day="2026-07-28"]').click();
   await page.waitForFunction(()=>!nvrLoading&&document.querySelector('[data-nvr-day]')?.dataset.nvrDay==='2026-07-28');
   await page.waitForFunction(()=>{const v=document.querySelector('video');return v?.readyState>=2&&!v.paused;});
   await page.evaluate(()=>{const v=document.querySelector('video');pauseRecording(v);window.previewVideo=v;window.previewSrc=v.src;});
   const slider=page.locator('[data-recording-playback-seek]');
   await slider.scrollIntoViewIfNeeded();await slider.hover();await page.waitForTimeout(250);
   assert.equal(await page.locator('[data-recording-scrub-preview]').count(),0,'hover thumbnail UI returned');
   async function frame(value){
    await slider.evaluate((slider,value)=>{slider.value=String(value);slider.dispatchEvent(new Event('input',{bubbles:true}));slider.dispatchEvent(new Event('change',{bubbles:true}));},value);
    await page.waitForFunction(value=>{const v=document.querySelector('video'),c=document.querySelector('[data-recording-transition]');return v.paused&&!v.seeking&&Math.abs(v.currentTime-value)<.1&&c.hidden&&recordingScrubs.size===0;},value);
    return page.locator('video').evaluate(v=>{const c=document.createElement('canvas');c.width=160;c.height=90;c.getContext('2d').drawImage(v,0,0,160,90);return c.toDataURL();});
   }
   const first=await frame(0);await page.evaluate(()=>previewSrc=previewVideo.src);const next=await frame(5);assert.notEqual(first,next,'main recording picture never changes');
   assert.equal(await frame(0),first,'backward scrub did not return to the original frame');
   const untouched=await page.evaluate(()=>({same:previewVideo===document.querySelector('video'),src:previewVideo.src===previewSrc,paused:previewVideo.paused}));
   assert(untouched.same&&untouched.src&&untouched.paused,`same-file scrub replaced/resumed the player: ${JSON.stringify(untouched)}`);
   await page.locator('[data-recording-fullscreen]').click();
   await page.waitForFunction(()=>document.fullscreenElement||document.querySelector('.edge-soft-fullscreen'));
   await frame(5);await frame(5);
   assert(await page.evaluate(()=>{const fullscreen=document.fullscreenElement||document.querySelector('.edge-soft-fullscreen');return fullscreen.contains(document.querySelector('[data-recording-playback-seek]'));}),'main seek bar disappeared in fullscreen');
   await page.evaluate(()=>document.fullscreenElement?document.exitFullscreen():exitSoftFullscreen('test'));
   assert.deepEqual(errors,[]);
   console.log(`PASS ${width}px: previous day preempts hung refresh, rapid day selection, forward/back native frames, no hover thumbnails, stable paused player and repeated fullscreen seek`);
   await page.unrouteAll({behavior:'ignoreErrors'});await context.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
