// The source is already running, halfway through a 24-second camera GOP.
const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  const page=await browser.newPage();page.setDefaultTimeout(45000);await page.goto('http://127.0.0.1:18089');
  const deadline=Date.now()+45000;let before;
  do{before=await page.evaluate(()=>fetch('/test/status').then(r=>r.json()));if(before.sources[0].gop_cache_packets>=240&&before.sources[0].gop_cache_packets<320)break;assert(Date.now()<deadline,'source never retained a long GOP');await page.waitForTimeout(100);}while(true);
  assert(before.sources[0].bootstrap_ready);
  await page.evaluate(async()=>{
   const {EdgeRTCClient}=await import('/edge/edge-rtc-client.js');const canvas=document.createElement('canvas');document.body.append(canvas);window.liveErrors=[];
   window.longGopClient=new EdgeRTCClient({canvas,url:'/api/edge/rtc/camera.front',onError:message=>liveErrors.push(message)});await longGopClient.start();
  });
  await page.waitForFunction(()=>longGopClient.frames>=10);
  const metrics=await page.evaluate(()=>longGopClient.diagnosticState());
  if(process.env.EDGE_RTC_TEST_RESPOND_PLI!=='1')assert(metrics.bootstrap_packets>=240,JSON.stringify(metrics));
  if(process.env.EDGE_RTC_TEST_RESPOND_PLI==='1'){
   assert(metrics.bootstrap_packets<40,'fresh keyframe was queued behind the old Cloud bootstrap');
   const status=await page.evaluate(()=>fetch('/test/status').then(r=>r.json()));
   assert(status.sources[0].keyframe_requests>=1);assert(status.sources[0].keyframe_observed_after_request_ms>0&&status.sources[0].keyframe_observed_after_request_ms<2000,'camera did not deliver the requested I-frame');
  }else assert(metrics.bootstrap_frames_skipped>=180);
  assert(metrics.first_frame_ms<5000,'waited for the next camera keyframe');
  assert.deepEqual(await page.evaluate(()=>liveErrors),[]);assert.equal((await page.evaluate(()=>fetch('/test/status').then(r=>r.json()))).sources[0].connection_attempts,1);
  await page.evaluate(()=>longGopClient.close());
  const idleBefore=await page.evaluate(()=>fetch('/test/status').then(r=>r.json()));
  await page.waitForTimeout(500);
  const idleAfter=await page.evaluate(()=>fetch('/test/status').then(r=>r.json()));
  assert.equal(idleAfter.sources[0].viewers,0);assert.equal(idleAfter.sources[0].upstream_connections,1);
  assert.equal(idleAfter.sources[0].connection_attempts,1);assert(idleAfter.sources[0].packets>idleBefore.sources[0].packets,'camera stopped when the last viewer left');
  assert(idleAfter.sources[0].bootstrap_ready,'continuous source lost its prepared GOP');
  // A blocked iframe HTML request must not delay the inline native player.
  const panel=await browser.newPage();let documentRequests=0;
  await panel.route('**/api/edge-rtc/player/**',route=>{documentRequests++;return route.abort('failed');});
  await panel.goto('http://127.0.0.1:18090');await panel.locator('[data-add-camera-tile]').waitFor({state:'attached'});
  await panel.evaluate(()=>{const f=document.createElement('iframe');f.className='live-frame';f.dataset.livePath='front_main';f.dataset.liveKey='inline';f.dataset.liveUrl=new URL('/api/edge-rtc/player/front_main',location.href).href;f.srcdoc=inlineLiveDocument(f.dataset.liveUrl);f.src=f.dataset.liveUrl;document.querySelector('#camera-grid').append(f);});
  try{await panel.waitForFunction(()=>document.querySelector('#camera-grid iframe')?.dataset.liveReady==='true');}catch(error){console.error('INLINE PLAYER',await panel.evaluate(()=>[...document.querySelectorAll('iframe')].map(f=>({src:f.src,inline:Boolean(f.srcdoc),text:f.contentDocument?.body?.textContent,base:f.contentDocument?.baseURI}))));throw error;}
  assert.equal(documentRequests,0,'inline player fetched a separate document');
  assert(await panel.locator('iframe').evaluate(f=>f.contentDocument.querySelector('canvas').width===640));
  assert.equal((await page.evaluate(()=>fetch('/test/status').then(r=>r.json()))).sources[0].connection_attempts,1);
  await panel.close();
  console.log(`PASS actual long-GOP RTSP -> HA proxy -> WebCodecs: ${metrics.bootstrap_packets} cached packets, first image ${Math.round(metrics.first_frame_ms)} ms, one camera upstream`);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
