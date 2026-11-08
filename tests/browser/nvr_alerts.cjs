const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  for(const [name,width,height] of [['desktop',1440,1000],['tablet',820,1180],['phone',390,844],['small-phone',320,740]]){
   const context=await browser.newContext({viewport:{width,height},hasTouch:width<1000,isMobile:width<1000});
   const page=await context.newPage();page.setDefaultTimeout(25000);const errors=[];page.on('pageerror',error=>errors.push(error.message));
   // JPEG extraction is deliberately unavailable: preview success must come
   // from the saved video, with no live/camera endpoint or server decoder.
   await page.route('**/recording-thumbs/**',route=>route.fulfill({status:415,body:'server extraction unsupported'}));
   await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088');
   await page.locator('[name="camera-0-name"]').waitFor({state:'attached'});
   const nav=async name=>{if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator(`nav [data-page-target="${name}"]`).click();};
   await nav('nvr');
   await page.waitForFunction(()=>document.querySelector('video[data-recording-player]')?.readyState>=2);
   await page.waitForFunction(()=>{const image=document.querySelector('.recording-film-grid img');return image?.naturalWidth>100&&image.src.startsWith('blob:');});
   await page.waitForFunction(()=>!document.querySelector('[data-recording-frame="1"]').disabled);
   const video=page.locator('video[data-recording-player]');await video.evaluate(v=>pauseRecording(v));
   const slider=page.locator('[data-recording-playback-seek]');
   await slider.evaluate(node=>{node.value='2';node.dispatchEvent(new Event('input',{bubbles:true}));node.dispatchEvent(new Event('change',{bubbles:true}));});
   await page.waitForFunction(()=>recordingScrubs.size===0&&!document.querySelector('video[data-recording-player]').seeking);
   const before=await video.evaluate(v=>v.currentTime);
   await page.locator('[data-recording-frame="1"]').click();
   await page.waitForFunction(before=>{const v=document.querySelector('video[data-recording-player]');return !v.seeking&&v.currentTime>before+.08&&v.currentTime<before+.13;},before);
   await page.locator('[data-recording-frame="-1"]').click();
   await page.waitForFunction(before=>Math.abs(document.querySelector('video[data-recording-player]').currentTime-before)<.02,before);
   // The visible picture must retain a real frame while a different MP4 loads.
   await page.route('**/recordings/**000007.mp4*',async route=>{await new Promise(resolve=>setTimeout(resolve,900));try{await route.continue();}catch(_){} });
   await slider.evaluate(node=>{node.value='9';node.dispatchEvent(new Event('input',{bubbles:true}));node.dispatchEvent(new Event('change',{bubbles:true}));});
   await page.waitForFunction(()=>{const video=document.querySelector('video[data-recording-player]');return video.readyState<2;});
   assert(await page.locator('[data-recording-transition]').evaluate(canvas=>!canvas.hidden&&canvas.dataset.valid==='true'&&canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data.some((value,index)=>index%4!==3&&value>20)),'loading replaced the held picture with black');
   await page.waitForFunction(()=>recordingScrubs.size===0&&!document.querySelector('video[data-recording-player]').seeking);
   await page.unroute('**/recordings/**000007.mp4*');
   const point=page.locator('[data-alert-point][data-alert-ids="3"]');await point.waitFor();
   assert.equal(await page.locator('[data-recording-playback-seek]').count(),1);
   await point.click();
   const image=page.locator('[data-alert-detail] [data-alert-watch="3"] img');await image.waitFor();
   await image.evaluate(image=>image.decode());
   assert(await image.evaluate(image=>image.naturalWidth>100));
   await page.locator('[data-alert-detail] [data-alert-clip="3"]').click();
   await page.waitForFunction(()=>document.querySelector('dialog video')?.readyState>=2);
   assert(await page.locator('dialog video').evaluate(video=>video.src.includes('/api/alerts/3/clip.mp4')));
   await page.locator('dialog button').click();
   await page.locator('[data-alert-detail] [data-alert-watch="3"]').click();
   await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player]');return v?.readyState>=2&&!v.seeking&&Math.abs(recordingPlaybackPosition(v)-6)<.3;});
   const clip=await page.evaluate(async()=>{const event=await fetch(panelPath('api/alerts/3')).then(r=>r.json());const response=await fetch(panelPath(event.clip_url),{headers:{Range:'bytes=0-127'}});return{event,status:response.status,length:(await response.arrayBuffer()).byteLength};});
   assert.equal(clip.event.clip_state,'ready');assert.equal(clip.status,206);assert.equal(clip.length,128);
   await video.evaluate(v=>pauseRecording(v));await page.locator('[data-recording-fullscreen]').click();
   await page.waitForFunction(()=>document.fullscreenElement||document.querySelector('.edge-soft-fullscreen'));
   await page.locator('[data-alert-point][data-alert-ids="3"]').click();
   await page.locator('[data-alert-popup] [data-alert-watch="3"] img').waitFor();
   assert(await page.evaluate(()=>{const full=document.fullscreenElement||document.querySelector('.edge-soft-fullscreen');return full.contains(document.querySelector('[data-alert-popup]'));}));
   await page.evaluate(()=>document.fullscreenElement?document.exitFullscreen():exitSoftFullscreen('test'));
   await nav('events');await page.waitForFunction(()=>!recordedPreviewVideo||!recordedPreviewVideo.getAttribute('src'));await page.locator('#event-kind-filter').selectOption('animal');
   await page.locator('#event-list [data-alert-watch="3"]').waitFor();
   assert.equal(await page.locator('#event-list [data-alert-watch]').count(),1);
   await nav('edge-settings');
   await page.locator('[data-easy-detection-camera="0"]').check();
   assert(await page.locator('[name="detection-local"]').isChecked());
   await page.locator('[name="detection-person"]').check();await page.locator('[name="detection-animal"]').check();
   await page.locator('[name="detection-notifications"]').evaluate(node=>node.closest('details').open=true);
   await page.locator('[name="detection-notifications"]').check();await page.locator('[name="detection-notify-service"]').fill('notify.mobile_app_phone');
   await page.locator('#save-edge-settings').click();await page.waitForFunction(()=>document.getElementById('edge-save-state').textContent.startsWith('Saved and verified.'));
   const saved=await page.evaluate(()=>fetch('api/config').then(r=>r.json()));
   assert(saved.detection.person_enabled&&saved.detection.animal_enabled&&saved.cameras[0].local_detection_enabled);
   assert(saved.detection.notifications_enabled);assert.equal(saved.detection.notify_service,'notify.mobile_app_phone');
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'alert controls overflow');
   assert.deepEqual(errors,[]);console.log(`PASS ${name}: browser thumbnails without server JPEG, frame steps, no black during load, intrusion dots/photos/exact seek/saved clip, fullscreen and simple phone settings`);
   await page.unrouteAll({behavior:'ignoreErrors'});await context.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
