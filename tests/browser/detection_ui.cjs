const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  for(const width of [1440,820,390,320]){
   const context=await browser.newContext({viewport:{width,height:1000},isMobile:width<=820,hasTouch:width<=820,permissions:['clipboard-read','clipboard-write']});
   const page=await context.newPage();page.setDefaultTimeout(15000);const errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088');
   await page.locator('[data-camera-form]').waitFor({state:'attached'});
   const nav=async target=>{if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator(`nav [data-page-target="${target}"]`).click();};
   const historyReady=()=>page.waitForFunction(()=>document.getElementById('event-list').getAttribute('aria-busy')==='false'&&document.getElementById('event-history-status').textContent.startsWith('Historia gotowa'));
   const selectFilter=async(id,value)=>{await page.locator(id).selectOption(value);await historyReady();};
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
   await nav('events');await historyReady();
   assert.equal(await page.locator('[data-event-recording]').count(),100);
   assert(!await page.locator('[data-event-recording="1"]').count());
   await page.locator('#events-older').click();await historyReady();
   await page.locator('#events-older').click();await historyReady();
   assert.equal(await page.locator('[data-event-recording]').count(),12);
   assert(await page.locator('[data-event-recording="1"]').count());
   assert(await page.locator('#events-older').isDisabled());
   assert((await page.locator('#event-page-label').textContent()).includes('Strona 3'));
   if(width===1440){
    await page.waitForTimeout(5300);
    assert((await page.locator('#event-page-label').textContent()).includes('Strona 3'),'metadata poll reset history');
    assert(await page.locator('[data-event-recording="1"]').count());
   }
   await page.locator('#events-newer').click();await historyReady();
   assert((await page.locator('#event-page-label').textContent()).includes('Strona 2'));
   await page.locator('#event-day-filter').fill('2026-07-27');await page.locator('#event-day-filter').dispatchEvent('change');await historyReady();
   assert.equal(await page.locator('[data-event-recording]').count(),1);
   await selectFilter('#event-kind-filter','vehicle');assert.equal(await page.locator('[data-event-recording]').count(),0);
   await selectFilter('#event-kind-filter','person');assert.equal(await page.locator('[data-event-recording]').count(),1);
   if(width===1440){
    let delayed=false;
    await page.route('**/api/events?**',async route=>{
     if(new URL(route.request().url()).searchParams.get('kind')==='vehicle'){
      delayed=true;await new Promise(resolve=>setTimeout(resolve,500));
      await route.fulfill({json:{events:[],next_before:null}}).catch(()=>{});
     }else await route.continue();
    });
    await page.locator('#event-kind-filter').selectOption('vehicle');await page.waitForFunction(()=>document.getElementById('event-list').getAttribute('aria-busy')==='true');
    await page.waitForTimeout(50);await selectFilter('#event-kind-filter','person');await page.waitForTimeout(600);
    assert(delayed);assert.equal(await page.locator('[data-event-recording="1"]').count(),1,'stale filter response replaced current history');
    await page.unrouteAll({behavior:'ignoreErrors'});
   }
   await page.locator('#event-notifications summary').click();
   await page.locator('#event-notify-service').fill('notify.mobile_app_phone;invalid');
   assert(await page.locator('#event-copy-notification').isDisabled());
   assert.equal(await page.locator('#event-notification-yaml').inputValue(),'');
   await page.locator('#event-notify-service').fill('notify.mobile_app_phone');
   const yaml=await page.locator('#event-notification-yaml').inputValue();
   for(const part of ['event_type: edge_event','camera_id: "front"',"['person', 'vehicle']",'action: notify.mobile_app_phone','seconds: 60','mode: single'])assert(yaml.includes(part),part);
   assert(!/rtsp:|secret|192\.0\.2/.test(yaml));
   await page.locator('#event-copy-notification').click();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),yaml);
   await page.locator('[data-event-recording="1"]').click();
   await page.waitForFunction(()=>!document.querySelector('[data-page="nvr"]').hidden&&document.querySelector('video[data-recording-player]'));
   await page.waitForFunction(()=>{const video=document.querySelector('video[data-recording-player]');if(!video||video.readyState<2||video.seeking)return false;video.pause();return Math.abs(Number(video.dataset.recordingPlaybackStart||0)+video.currentTime-8)<1.5;});
   await page.locator('[data-recording-event-slot]').waitFor();
   assert.equal(await page.locator('[data-recording-event-slot]').count(),1);
   await page.evaluate(()=>{window.eventVideo=document.querySelector('video[data-recording-player]');window.eventVideoSrc=eventVideo.src;});
   await page.locator('[data-recording-event-select]').selectOption('0');
   assert(await page.evaluate(()=>eventVideo===document.querySelector('video[data-recording-player]')));
   await page.locator('[data-recording-events-refresh]').click();
   await page.waitForTimeout(300);
   assert(await page.evaluate(()=>eventVideo===document.querySelector('video[data-recording-player]')&&eventVideo.paused&&eventVideo.src===eventVideoSrc),'event refresh changed paused video');
   if(width===1440){
    let delayedTimeline=false;
    await page.route('**/api/events/timeline?**',async route=>{
     if(new URL(route.request().url()).searchParams.get('day')==='2026-07-28'){delayedTimeline=true;await new Promise(resolve=>setTimeout(resolve,500));}
     await route.continue().catch(()=>{});
    });
    await page.locator('[data-recording-day="2026-07-28"]').click();
    await page.waitForFunction(()=>document.querySelector('[data-nvr-day]')?.dataset.nvrDay==='2026-07-28');
    await page.locator('[data-recording-events-refresh]').click();
    await page.waitForTimeout(100);
    await page.locator('[data-recording-day="2026-07-27"]').click();
    await page.waitForFunction(()=>document.querySelector('[data-nvr-day]')?.dataset.nvrDay==='2026-07-27');
    await page.waitForTimeout(650);assert(delayedTimeline);
    assert.equal(await page.locator('[data-recording-event-slot]').count(),1,'stale timeline replaced selected day');
    await page.unrouteAll({behavior:'ignoreErrors'});
   }
   await page.locator('[data-recording-day="2026-07-28"]').click();
   await page.waitForFunction(()=>document.querySelector('[data-nvr-day]')?.dataset.nvrDay==='2026-07-28');
   await page.waitForFunction(()=>document.querySelectorAll('[data-recording-event-slot]').length>1);
   await page.locator('[data-recording-event-select]').selectOption('1');
   assert((await page.locator('[data-recording-event-message]').textContent()).includes('Brak nagrania'));
   await page.locator('[data-recording-day="2026-07-27"]').click();
   await page.waitForFunction(()=>document.querySelector('[data-nvr-day]')?.dataset.nvrDay==='2026-07-27'&&document.querySelectorAll('[data-recording-event-slot]').length===1);
   await page.locator('[data-recording-event-history]').click();await historyReady();
   assert.equal(await page.locator('#event-day-filter').inputValue(),'2026-07-27');
   assert.equal(await page.locator('#event-camera-filter').inputValue(),'front');
   assert.equal(await page.locator('[data-event-recording]').count(),1);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   assert.deepEqual(errors,[]);
   if(process.env.EDGE_EVENT_SCREENSHOT_DIR)await page.screenshot({path:`${process.env.EDGE_EVENT_SCREENSHOT_DIR}/events-${width}.png`,fullPage:true});
   console.log(`PASS detection ${width}px: settings, deep history/paging, date/type filters, notification YAML/clipboard, actual NVR seek, day markers and stable paused video`);
   await context.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
