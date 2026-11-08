// Native 16 kHz AAC, actual PCM output, unexpected pause and cached day return.
const {chromium,webkit}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18091';
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:wk?'Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148':'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/151 Mobile Safari/537.36'});
  const page=await context.newPage();page.setDefaultTimeout(20000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/edge-rtc/player/**',r=>r.fulfill({contentType:'text/html',body:'<p>Isolated source</p>'}));
  await page.goto(base);await page.locator('[data-camera-form]').first().waitFor({state:'attached'});
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
  const video=page.locator('video[data-recording-player="1"]');await video.waitFor({state:'attached'});
  await page.locator('[data-recording-toggle="1"]').click();
  await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player="1"]');return !v.paused&&v.videoWidth>0&&v.currentTime>.2;});
  await page.locator('[data-recording-mute="1"]').evaluate(button=>button.addEventListener('click',()=>{
   const video=document.querySelector('video[data-recording-player="1"]');window.voiceContext=new AudioContext();window.voiceAnalyser=voiceContext.createAnalyser();
   voiceContext.createMediaElementSource(video).connect(voiceAnalyser);voiceAnalyser.connect(voiceContext.destination);voiceContext.resume();
  },{once:true}));
  await page.locator('[data-recording-more="1"]').click();await page.locator('[data-recording-mute="1"]').click();
  await page.waitForFunction(()=>{const data=new Float32Array(voiceAnalyser.fftSize);voiceAnalyser.getFloatTimeDomainData(data);return data.some(v=>Math.abs(v)>.01);});
  const timing=await page.evaluate(()=>fetch(panelPath('api/recording/timing?'+new URLSearchParams({key:recordingStatus[1].key,name:recordingStatus[1].files[0].name}))).then(r=>r.json()));
  assert.equal(timing.audio_sample_rate,16000);assert.equal(timing.audio_codec,'mp4a');assert(timing.audio_clock_regular);
  await video.evaluate(v=>v.pause());
  await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player="1"]');return !v.paused&&v.dataset.recordingWantsPlay==='true';});
  await video.evaluate(v=>{v.pause();pauseRecording(v);});await page.waitForTimeout(250);assert(await video.evaluate(v=>v.paused));
  await page.evaluate(()=>selectRecordingDay(1,'2026-07-27','test'));
  await page.waitForFunction(()=>recordingStatus[1]?.selected_day==='2026-07-27'&&document.querySelector('[data-nvr-index="1"]').dataset.nvrDay==='2026-07-27');
  let held;const blocked=new Promise(resolve=>{held=resolve;});let routeToRelease;
  await page.route('**/api/recording/status*',route=>{
   if(new URL(route.request().url()).searchParams.get('days')?.includes('1:2026-07-28')){routeToRelease=route;held();return;}
   return route.continue();
  });
  await page.evaluate(()=>selectRecordingDay(1,'2026-07-28','test'));await blocked;
  assert.equal(await page.locator('[data-nvr-index="1"]').getAttribute('data-nvr-day'),'2026-07-28','cached day waited for the blocked server response');
  await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player="1"]');return !v.paused&&v.videoWidth>0&&v.currentTime>.2;});
  await video.evaluate(v=>window.keptDayPlayer=v);await page.evaluate(()=>selectRecordingDay(1,'2026-07-28','test'));
  assert(await video.evaluate(v=>v===keptDayPlayer&&!v.paused),'repeated day selection reset playback');
  await routeToRelease.continue();await page.unrouteAll({behavior:'ignoreErrors'});await page.evaluate(()=>voiceContext.close());
  assert.deepEqual(errors,[]);console.log(`PASS ${wk?'webkit':'chromium'}: actual 16 kHz AAC PCM, native pause recovery respects Pause, cached day plays before server response, repeated day preserves player`);
  await context.close();
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
