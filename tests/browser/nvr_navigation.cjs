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
   async function preview(value){
    await page.locator('[data-recording-playback-seek]').evaluate((slider,value)=>{const r=slider.getBoundingClientRect();slider.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerType:'mouse',clientX:r.left+6+(r.width-12)*value/Number(slider.max),buttons:0}));},value);
    await page.waitForFunction(()=>{const box=document.querySelector('[data-recording-scrub-preview]');const img=box?.querySelector('img');return box&&!box.hidden&&img&&!img.hidden&&img.naturalWidth>100;});
    return page.locator('[data-recording-scrub-preview] img').evaluate(img=>img.src);
   }
   const first=await preview(0),next=await preview(5);assert.notEqual(first,next,'scrub image never changes');
   assert.equal(await preview(0),first,'backward scrub should reuse the matching image');
   const untouched=await page.evaluate(()=>({same:previewVideo===document.querySelector('video'),src:previewVideo.src===previewSrc,paused:previewVideo.paused}));
   assert(untouched.same&&untouched.src&&untouched.paused,`preview changed the playing video: ${JSON.stringify(untouched)}`);
   const box=await page.locator('[data-recording-scrub-preview]').boundingBox();
   const viewport=await page.evaluate(()=>({width:innerWidth,height:innerHeight}));
   assert(box.x>=0&&box.y>=0&&box.x+box.width<=viewport.width+1&&box.y+box.height<=viewport.height+1,'preview outside viewport');
   const jpeg=await page.evaluate(async()=>{
    const f=recordingStatus[0].files[0],url=panelPath(f.thumbnail_url);
    return await Promise.all([0,5].map(async at=>{const response=await fetch(`${url}&at=${at}`);if(!response.ok)throw new Error(`thumbnail ${response.status}`);return Array.from(new Uint8Array(await response.arrayBuffer()));}));
   });
   assert(jpeg[0].length>512&&jpeg[1].length>512);assert.notDeepEqual(jpeg[0],jpeg[1],'server returned the same frame for different times');
   await page.locator('[data-recording-fullscreen]').click();
   await page.waitForFunction(()=>document.fullscreenElement||document.querySelector('.edge-soft-fullscreen'));
   await preview(5);
   assert(await page.evaluate(()=>{const fullscreen=document.fullscreenElement||document.querySelector('.edge-soft-fullscreen');return fullscreen.contains(document.querySelector('[data-recording-scrub-preview]'));}),'scrub preview disappeared outside fullscreen wrapper');
   await page.evaluate(()=>document.fullscreenElement?document.exitFullscreen():exitSoftFullscreen('test'));
   assert.deepEqual(errors,[]);
   console.log(`PASS ${width}px: previous day preempts hung refresh, rapid day selection, forward/back scrub changes actual JPEG without touching player`);
   await page.unrouteAll({behavior:'ignoreErrors'});await context.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
