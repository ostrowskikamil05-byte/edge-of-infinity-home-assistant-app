const {chromium,webkit}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const engine=process.env.EDGE_TEST_BROWSER||'chromium',wk=engine==='webkit';
 const browser=await (wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});page.setDefaultTimeout(30000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/edge-rtc/player/**',r=>r.fulfill({contentType:'text/html',body:'<p>Generated fixture</p>'}));
  await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18091');await page.locator('[data-camera-form]').first().waitFor({state:'attached'});
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
  await page.locator('[data-recording-more="0"]').click();await page.locator('[data-recording-codecs="0"]').click();
  const dialog=page.locator('dialog');await dialog.waitFor();
  await dialog.locator('[name="video"]').selectOption('h264');
  async function prepare(audio){
   await dialog.locator('[name="audio"]').selectOption(audio);
   const response=page.waitForResponse(r=>r.url().endsWith('/api/recording/test')&&r.request().method()==='POST');
   await dialog.locator('button[type="submit"]').click();const res=await response;assert(res.ok(),await res.text());return res.json();
  }
  const aac=await prepare('aac');assert.equal(aac.report.input.codec_name,'hevc');assert(aac.report.video_transcoded);
  await page.waitForFunction(()=>{const v=document.querySelector('dialog video');return v&&!v.paused&&v.currentTime>.1&&v.videoWidth===640;});
  const output=await dialog.locator('video').evaluate(v=>({width:v.videoWidth,height:v.videoHeight}));assert.deepEqual(output,{width:640,height:360});
  assert.equal(aac.report.output.fps,20);assert.equal(aac.report.measured_streams.find(s=>s.codec_type==='audio').codec_name,'aac');
  const silent=await prepare('none');assert(!silent.report.measured_streams.some(s=>s.codec_type==='audio'));
  await page.waitForFunction(()=>document.querySelector('dialog video').currentTime>.1);
  const law=await prepare('alaw');assert.equal(law.report.measured_streams.find(s=>s.codec_type==='audio').codec_name,'pcm_alaw');
  await page.waitForFunction(()=>{const v=document.querySelector('dialog video');return !!v.error||(!v.paused&&v.currentTime>.1);});
  await dialog.locator('[data-codec-close]').click();assert.equal(await page.locator('dialog').count(),0);
  const logs=await (await page.request.get((process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18091')+'/api/logs')).json();
  assert(/recording_codec_test_(done|cached)/.test(logs.playback_log));let deviceLog;for(let i=0;i<40;i++){const id=await page.evaluate(()=>deviceReporter.device_id);deviceLog=await(await page.request.get((process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18091')+'/api/client-diagnostics?map=0&device='+id)).json();if(deviceLog.selected?.log.includes('ui_codec_test_'))break;await page.waitForTimeout(250);}assert(deviceLog.selected?.log.includes('ui_codec_test_'));assert(logs.hardware.inventory.cpu_model);
  // Reproduce a paused browser while the recording is still buffering.
  await page.evaluate(()=>{const v=document.querySelector('video[data-recording-player="1"]');restoreRecordingPlayer(v);v.dataset.recordingWantsPlay='true';v.dataset.recordingBuffering='true';v.pause();});
  const slider=page.locator('[data-recording-playback-seek="1"]');
  await slider.evaluate(s=>{s.value='8';s.dispatchEvent(new Event('input',{bubbles:true}));s.dispatchEvent(new Event('change',{bubbles:true}));});
  await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player="1"]');return !v.paused&&recordingScrubs.size===0&&v.currentTime>.1;});
  await page.evaluate(()=>{const v=document.querySelector('video[data-recording-player="1"]');v.dataset.recordingBuffering='true';v.pause();watchRecordingProgress(v);recordingProgressWatch.get(v).at=performance.now()-9000;watchRecordingProgress(v);});
  await page.waitForFunction(()=>!document.querySelector('video[data-recording-player="1"]').paused);
  await page.evaluate(()=>{const v=document.querySelector('video[data-recording-player="1"]');pauseRecording(v);watchRecordingProgress(v);});
  assert(await page.locator('video[data-recording-player="1"]').evaluate(v=>v.paused&&v.dataset.recordingWantsPlay==='false'));
  const latest=await (await page.request.get((process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18091')+'/api/logs')).json();
  assert(latest.playback.direct>0,'valid H264/AAC original was not served directly');
  assert(await page.locator('video[data-recording-player="1"]').evaluate(v=>new URL(v.dataset.recordingSrc).searchParams.get('container')==='aac-clock-v6'));
  assert.deepEqual(errors,[]);console.log(`PASS ${engine}: actual HEVC→H264/AAC/silent/G711 test clips, original dimensions/FPS, hardware/playback logs, buffered scrub resumes, bounded progress recovery respects Pause`);
  await page.unrouteAll({behavior:'ignoreErrors'});await page.close();
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
