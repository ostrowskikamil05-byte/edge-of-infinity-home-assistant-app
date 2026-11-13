const {chromium,webkit}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18092';
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/edge-rtc/player/**',r=>r.fulfill({contentType:'text/html',body:'<p>Generated camera</p>'}));
  await page.goto(base);await page.locator('[data-camera-form]').first().waitFor({state:'attached'});
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="logs"]').click();
  await page.waitForFunction(()=>panelLogs?.generated_at);
  await page.locator('#log-time-filter').selectOption('all');
  await page.evaluate(()=>{
   const events=[{event:'ui_recording_video_error',time:'2026-11-08T12:02:00Z',data:{index:0,error_code:3,message:'<img src=x onerror="window.logInjected=1">'}},
    {event:'ui_recording_video_waiting',time:'2026-11-08T12:01:00Z',data:{index:0}},
    {event:'rtc_first_frame',time:'2026-11-08T12:03:00Z',data:{index:0,first_frame_ms:350}}];
   panelLogs.playback_log=JSON.stringify({events});panelLogs.edge_debug='';
   for(const field of ['recording_thumbnail_log','recording_cache_log','recording_stream_log','camera_discovery_log'])panelLogs[field]='';renderLogs();
  });
  await page.locator('#log-group-filter').selectOption('recordings');
  assert.equal(await page.locator('#logs-view [data-log-event]').count(),1);
  assert.equal(await page.locator('#logs-view [data-log-event="ui_recording_video_error"]').getAttribute('open'),null);
  await page.locator('#log-level-filter').selectOption('all');assert.equal(await page.locator('#logs-view [data-log-event]').count(),2);
  await page.locator('#log-search').fill('bufor');assert.equal(await page.locator('#logs-view [data-log-event]').count(),1);
  await page.locator('#log-search').fill('');await page.locator('#log-group-filter').selectOption('live');
  assert.equal(await page.locator('#logs-view [data-log-event]').count(),1);
  assert.equal(await page.locator('#logs-view [data-log-category="live"]').count(),1);
  await page.locator('#log-group-filter').selectOption('recordings');await page.locator('#logs-view [data-log-event="ui_recording_video_error"] > summary').click();
  assert.equal(await page.evaluate(()=>window.logInjected),undefined);
  await page.evaluate(()=>{
   panelLogs.recording_cache_log=JSON.stringify({ts:'2001-01-01T12:00:00+0000',key:'front_0',error:'historical fixture disk error'});
   panelLogs.edge_debug=JSON.stringify({event:'ui_recording_video_error',ts:new Date().toISOString(),index:1,error_code:3});renderLogs();
  });
  await page.locator('#log-time-filter').selectOption('all');assert.equal(await page.locator('#logs-view [data-log-origin="archive"]').count(),1);
  await page.locator('#log-time-filter').selectOption('boot');assert.equal(await page.locator('#logs-view [data-log-origin="archive"]').count(),0,'an old cache failure appeared as a current error');
  await page.locator('#log-time-filter').selectOption('all');await page.locator('#log-camera-filter').selectOption('front');
  assert.equal(await page.locator('#logs-view [data-log-event="ui_recording_video_error"]').count(),1,'camera filter mixed backend camera IDs');
  await page.evaluate(()=>{deviceReport={selected:{latest:{device:{label:'Fixture phone'}},log:JSON.stringify({events:[{event:'rtc_error',time:new Date().toISOString(),data:{source:'camera.front',message:'test live failure'}}]})}};renderDeviceLogs();});
  await page.locator('#log-group-filter').selectOption('all');await page.locator('#log-origin-filter').selectOption('device');
  assert.equal(await page.locator('#logs-view [data-log-event]').count(),0);assert.equal(await page.locator('#device-log-view [data-log-event="rtc_error"]').count(),1,'device/session source filter lost the actual error');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'log rows overflow a phone');assert.deepEqual(errors,[]);
  console.log(`PASS ${wk?'webkit':'chromium'}: module/severity/camera/time/source/search filters, separate legacy history, escaped details and phone layout`);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
