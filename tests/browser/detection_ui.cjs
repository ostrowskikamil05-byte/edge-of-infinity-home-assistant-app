const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  for(const width of [1440,820,390,320]){
   const context=await browser.newContext({viewport:{width,height:1000}});
   const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088');
   await page.locator('[data-camera-form]').waitFor({state:'attached'});
   const nav=async target=>{if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator(`nav [data-page-target="${target}"]`).click();};
   await nav('camera-settings');
   await page.locator('[name="camera-0-events-enabled"]').check();
   await page.locator('[name="camera-0-local-detection-enabled"]').check();
   await nav('edge-settings');
   await page.locator('[name="detection-camera-events"]').check();
   await page.locator('[name="detection-local"]').check();
   await page.locator('[name="detection-fps"]').fill('0.4');
   await page.locator('#save-edge-settings').click();
   await page.waitForFunction(()=>document.getElementById('edge-save-state').textContent.startsWith('Saved and verified.'));
   const saved=await page.evaluate(()=>fetch('api/config').then(r=>r.json()));
   assert(saved.cameras[0].events_enabled);assert(saved.cameras[0].local_detection_enabled);assert(saved.detection.local_enabled);assert.equal(saved.detection.max_fps,.4);
   await nav('events');
   await page.locator('[data-event-recording]').first().waitFor();
   assert((await page.locator('#event-list').textContent()).includes('person'));
   await page.locator('#event-kind-filter').selectOption('vehicle');assert.equal(await page.locator('[data-event-recording]').count(),0);
   await page.locator('#event-kind-filter').selectOption('person');
   await page.locator('[data-event-recording]').first().click();
   await page.waitForFunction(()=>!document.querySelector('[data-page="nvr"]').hidden&&document.querySelector('video[data-recording-player]'));
   await page.waitForFunction(()=>{const video=document.querySelector('video[data-recording-player]');return video?.dataset.recordingSeek==='1'||video?.dataset.recordingStreamStart==='8';});
   await page.waitForFunction(()=>{const video=document.querySelector('video[data-recording-player]');if(!video||video.readyState<2||video.seeking)return false;video.pause();return Math.abs(Number(video.dataset.recordingPlaybackStart||0)+video.currentTime-8)<1.5;});
   assert.equal(await page.locator('video[data-recording-player]').count(),1);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   assert.deepEqual(errors,[]);
   console.log(`PASS detection ${width}px: per-camera/global settings, persistence, event filters and actual NVR seek`);
   await context.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
