const {chromium,webkit}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',engine=wk?webkit:chromium;
 const browser=await engine.launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 const base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18091';
 try{
  const ids=[];
  for(const [label,width,height] of [['Phone Kamil',390,844],['Tablet Home',820,1180]]){
   const context=await browser.newContext({viewport:{width,height},isMobile:true,hasTouch:true,acceptDownloads:true});
   const page=await context.newPage();page.setDefaultTimeout(30000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/api/edge-rtc/player/**',r=>r.fulfill({contentType:'text/html',body:'<p>Generated fixture</p>'}));
   await page.goto(base+'/?authSig=forbidden-signature');await page.waitForFunction(()=>deviceReporter?.device_id);
   const identity=await page.evaluate(()=>({device:deviceReporter.device_id,session:deviceReporter.session_id}));ids.push(identity.device);
   await page.evaluate(label=>deviceReporter.setLabel(label),label);
   await page.evaluate(()=>localStorage.setItem('edge-device-label','Shared device label'));
   assert.equal(await page.evaluate(()=>deviceReporter.device().label),'Shared device label','another panel/card context left a stale device label');
   await page.evaluate(label=>deviceReporter.setLabel(label),label);
   await page.route('**/api/version?token=forbidden-token',async route=>{await new Promise(resolve=>setTimeout(resolve,150));await route.fulfill({json:{version:'test'}});});
   await page.evaluate(async()=>{await fetch('api/version?token=forbidden-token');deviceReporter.note('test_device_error',{url:location.href,password:'forbidden-password'});});
   let data;
   for(let i=0;i<80;i++){
    data=await (await page.request.get(base+'/api/client-diagnostics?map=0&device='+identity.device)).json();
    if(data.selected?.latest.device.label===label&&data.selected.log.includes('test_device_error'))break;
    await page.waitForTimeout(250);
   }
   assert(data.selected?.log.includes('test_device_error'),'device report did not reach the server');
   assert(!JSON.stringify(data).includes('forbidden'));assert(data.selected.latest.device.capabilities);assert(data.selected.latest.device.unavailable.includes('CPU'));
   assert(data.selected.log.includes('duration_ms'),'resource timings were not retained');
   if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="logs"]').click();
   await page.locator('#device-log-select option').first().waitFor({state:'attached'});assert((await page.locator('#device-log-view').textContent()).includes('Connection map'));
   await page.locator('.ui-device-panel > summary').click();
   const download=page.waitForEvent('download');await page.locator('#device-log-download').click();const file=await download;assert(file.suggestedFilename().endsWith('.json'));
   assert(!require('node:fs').readFileSync(await file.path(),'utf8').includes('forbidden'),'export included an authorization value');
   await page.reload();await page.waitForFunction(()=>deviceReporter?.device_id);
   assert.deepEqual(await page.evaluate(()=>({device:deviceReporter.device_id,session:deviceReporter.session_id})),identity);
   data=await (await page.request.get(base+'/api/client-diagnostics?map=0')).json();assert(data.devices.some(d=>d.device_id===identity.device));
   assert.deepEqual(errors,[]);await page.unrouteAll({behavior:'ignoreErrors'});await page.close();await context.close();
  }
  assert.notEqual(ids[0],ids[1]);console.log(`PASS ${wk?'webkit':'chromium'}: separate phone/tablet identities and sessions, timings/errors/capabilities, redacted URLs, device selector/network map and downloadable report`);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
