const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  for(const [name,width,height] of [['desktop',1440,1000],['tablet',820,1180],['phone',390,844],['small-phone',320,740]]){
   const context=await browser.newContext({viewport:{width,height},isMobile:width<1000,hasTouch:width<1000});
   const page=await context.newPage();page.setDefaultTimeout(20000);const errors=[],requests=[];
   page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.url().includes('/recordings/'))requests.push(request.url());});
   await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088');
   await page.locator('[data-camera-form]').waitFor({state:'attached'});if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
   await page.waitForFunction(()=>document.querySelector('video[data-recording-player]')?.readyState>=2);
   await page.waitForFunction(()=>{const images=[...document.querySelectorAll('.recording-film-grid img')];return images.length>0&&images.every(image=>image.complete&&image.naturalWidth>100&&image.closest('.recording-thumb-wrap')?.classList.contains('thumb-loaded'));});
   assert(await page.locator('.recording-film-grid img').first().evaluate(image=>image.dataset.recordingThumbBackend==='jpeg'),'tiles fetched/decoded full 4K clips instead of small JPEGs');
   assert(requests.every(url=>url.includes('000000.mp4')),'non-playing clips were downloaded for tile thumbnails');
   const indexed=await page.evaluate(()=>{
    const files=Array.from({length:12000},(_,i)=>({url:`test-${i}`,name:String(i),start_ts:i*10,timeline_offset:i*10,playback_offset:i*10,duration_seconds:10})).reverse();
    const status={timeline:{day_total_seconds:120000}};let sorts=0;const sort=sortedRecordingFiles;sortedRecordingFiles=(...args)=>{sorts++;return sort(...args);};
    try{const start=performance.now(),timeline=recordingTimeline(files,status);let valid=true;for(let i=0;i<2000;i++){const cached=recordingTimeline(files,status),value=(i*59)%119999;valid=valid&&cached===timeline&&recordingTargetInTimeline(cached,value).target.file.url===`test-${Math.floor(value/10)}`;}
     const elapsed=performance.now()-start,sortCount=sorts,changed=recordingTimeline(files,{timeline:{day_total_seconds:130000}});return{sortCount,elapsed,valid,invalidated:changed!==timeline};
    }finally{sortedRecordingFiles=sort;}
   });
   assert(indexed.valid&&indexed.invalidated);assert.equal(indexed.sortCount,1);assert(indexed.elapsed<1500,indexed);
   const refresh=await page.evaluate(async()=>{
    const before=recordingStatus[0],video=document.querySelector('video[data-recording-player]'),timeline=recordingTimeline(before.files,before);
    const query=new URLSearchParams({days:'0:2026-07-28',known:`0:${before.files_revision}`});const data=await fetch(panelPath(`api/recording/status?${query}`)).then(response=>response.json());
    await loadRecordingStatus({reason:'timer'});
    return{omitted:!('files' in data.cameras[0])&&data.cameras[0].files_unchanged,same:recordingStatus[0].files===before.files&&document.querySelector('video[data-recording-player]')===video&&recordingTimeline(recordingStatus[0].files,recordingStatus[0])===timeline};
   });assert(refresh.omitted&&refresh.same,'status polling retransmitted/rebuilt the unchanged recording index');
   const video=page.locator('video[data-recording-player]'),slider=page.locator('[data-recording-playback-seek]'),toggle=page.locator('[data-recording-toggle]');
   await video.evaluate(video=>pauseRecording(video));
   await page.route('**/recordings/**000007.mp4*',async route=>{await new Promise(resolve=>setTimeout(resolve,650));await route.continue().catch(()=>{});});
   const seek=value=>slider.evaluate((slider,value)=>{slider.value=String(value);slider.dispatchEvent(new Event('input',{bubbles:true}));slider.dispatchEvent(new Event('change',{bubbles:true}));},value);
   await seek(9);await page.waitForFunction(()=>document.querySelector('video[data-recording-player]').readyState<2);
   await toggle.click(); // Play while the final seek is still loading.
   await page.waitForFunction(()=>{const video=document.querySelector('video[data-recording-player]');return !recordingScrubs.size&&!video.paused&&recordingPlaybackPosition(video)>=9&&recordingPlaybackPosition(video)<12;});
   // A new drag during an unfinished seek must retain the original play intent.
   await seek(16);await seek(2);await seek(10);
   await page.waitForFunction(()=>{const video=document.querySelector('video[data-recording-player]');return !recordingScrubs.size&&!video.paused&&recordingPlaybackPosition(video)>=10&&recordingPlaybackPosition(video)<13;});
   // Application Pause marks intent before the queued native pause event.
   // A seek in the same task must preserve that explicit user decision.
   await video.evaluate(video=>{pauseRecording(video);const slider=document.querySelector('[data-recording-playback-seek]');slider.value='1';slider.dispatchEvent(new Event('input',{bubbles:true}));slider.dispatchEvent(new Event('change',{bubbles:true}));});
   await page.waitForFunction(()=>!recordingScrubs.size&&document.querySelector('video[data-recording-player]').paused);
   await page.unroute('**/recordings/**000007.mp4*');
   // Chrome's AbortError + goo.gl link is expected when a load supersedes
   // play(). Keep intent and resume on canplay; never display the raw message.
   await video.evaluate(video=>{const original=video.play.bind(video);let first=true;video.play=()=>{if(first){first=false;video.load();return Promise.reject(new DOMException('The play() request was interrupted by a new load request. https://goo.gl/LdLk22','AbortError'));}return original();};});
   await toggle.click();await page.waitForFunction(()=>!document.querySelector('video[data-recording-player]').paused);
   assert(!/goo\.gl|interrupted|request to http/i.test(await page.locator('[data-recording-playback-message]').textContent()));
   await video.evaluate(video=>pauseRecording(video));
   await page.route('**/api/version',route=>route.fulfill({json:{version:'0.11.99',ui_build:'new'}}));await page.evaluate(()=>checkPanelVersion());
   assert((await page.locator('[data-panel-update]').textContent()).includes('0.11.99'));
   assert(await video.evaluate(video=>video.paused),'version check changed playback');
   assert.deepEqual(errors,[]);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   console.log(`PASS ${name}: small JPEG tiles, one index for 12000 clips (${Math.round(indexed.elapsed)} ms), Play during slow seek, rapid playing seeks, interrupted play recovery and stale-panel notice`);
   await page.unrouteAll({behavior:'ignoreErrors'});await context.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
