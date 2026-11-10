const {chromium,webkit}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18092';
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{for(const mobile of(wk?[true]:[true,false])){
  const context=await browser.newContext({viewport:{width:mobile?430:1440,height:mobile?799:1000},isMobile:mobile,hasTouch:mobile,
   userAgent:mobile?(wk?'Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/HomeAssistant':'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/151 Mobile Safari/537.36'):undefined});
  const page=await context.newPage();page.setDefaultTimeout(20000);const requests=[],errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/recording/status'))requests.push(r.url());});
  await page.route('**/api/edge-rtc/player/**',r=>r.fulfill({contentType:'text/html',body:'<p>Generated source</p>'}));
  await page.goto(base);await page.locator('[data-camera-form]').first().waitFor({state:'attached'});await page.waitForTimeout(150);
  assert(requests.length&&requests.every(url=>new URL(url).searchParams.get('summary')==='1'),'Live boot loaded the full video archive');
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();
  const responsePromise=page.waitForResponse(r=>r.url().includes('/api/recording/status')&&!new URL(r.url()).searchParams.has('summary'));
  await page.locator('nav [data-page-target="nvr"]').click();const response=await responsePromise;
  assert.equal(response.headers()['content-encoding'],'gzip','large index was sent without gzip');
  await page.waitForFunction(()=>{const s=recordingStatus[0];return s?.segments>=12000&&s.files?.length>3000;});
  const video=page.locator('[data-dvr-host="0"]');await video.waitFor();await video.locator('[data-dvr-action="toggle"]').click();
  await page.waitForFunction(()=>{const p=archivePlayers.get('0');return p?.client?.picture&&p.wantPlay&&p.position>.1;});
  assert.notEqual(await page.locator('.rec-badge').first().textContent(),'OFF');
  assert(!await page.locator('[data-storage-compact]').textContent().then(s=>s.includes('Odczyt')));
  await page.evaluate(()=>window.archiveVideo=archivePlayers.get('0'));
  await page.route('**/api/recording/status*',route=>new URL(route.request().url()).searchParams.has('known')?route.abort('timedout'):route.continue());
  await page.evaluate(()=>loadRecordingStatus({reason:'timer'}));
  assert(await page.evaluate(()=>archivePlayers.get('0')===archiveVideo&&archiveVideo.wantPlay&&!archiveVideo.client.paused),'background refresh stopped/replaced playback');
  assert.equal(await page.locator('[data-recording-day-load]:visible').count(),0,'background polling displayed a false playback failure');
  assert.deepEqual(errors,[]);console.log(`PASS ${wk?'webkit':'chromium'} ${mobile?'phone':'desktop'}: lightweight Live boot, immediate 12000-file archive with gzip, actual playback and non-disruptive failed refresh`);
  await page.unrouteAll({behavior:'ignoreErrors'});await page.close();await context.close();
 }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
