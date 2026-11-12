// Real HA bridge, IndexedDB/JPEG cache and native DVR; a held catalog cannot hide a ready gallery.
const {chromium,webkit}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',base=process.env.EDGE_HA_BRIDGE_URL||'http://127.0.0.1:18195';
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});let page;
 try{
  const context=await browser.newContext({viewport:{width:430,height:900},isMobile:true,hasTouch:true});page=await context.newPage();page.setDefaultTimeout(20000);
  const errors=[],commands=[],delayed=[];let hold=false,blocked=false,catalogRequests=0,denied=false,held=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/edge/dashboard/recordings/**',async route=>{catalogRequests++;if(denied)return route.fulfill({status:403,contentType:'application/json',body:'{"error":"forbidden"}'});if(blocked)await new Promise(resolve=>held.push(resolve));await route.continue();});
  await page.routeWebSocket('**/api/edge/dashboard/archive/**',socket=>{const server=socket.connectToServer();socket.onMessage(message=>{if(typeof message==='string')commands.push(JSON.parse(message));server.send(message);});server.onMessage(message=>{if(hold)delayed.push(()=>socket.send(message));else socket.send(message);});});
  const url=base+'/?cards=recordings&recordings='+encodeURIComponent(JSON.stringify({cameras:['camera.back'],muted:true}));
  await page.goto(url);await page.waitForFunction(()=>document.querySelector('edge-recordings-card')._files.length===3);
  await page.waitForFunction(()=>[...document.querySelector('edge-recordings-card').shadowRoot.querySelectorAll('.moment img')].every(image=>image.naturalWidth>0));
  await page.waitForFunction(async()=>{const store=await edgeDashboardStore();await store.writes;return Boolean(await store.loadDashboard('ha:fixture-dashboard-user|camera.back|latest'));});
  assert.equal(catalogRequests,1,'startup duplicated its catalog request');
  blocked=true;catalogRequests=0;const started=Date.now();await page.reload({waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>document.querySelector('edge-recordings-card')?._files.length===3,{},{timeout:1400});
  const restoreMS=Date.now()-started;assert.equal(held.length,1,'catalog was not held for the cache test');
  await page.evaluate(()=>window.rec=document.querySelector('edge-recordings-card'));
  assert.equal(await page.evaluate(()=>rec.shadowRoot.querySelector('.player').hidden),false);
  await page.waitForFunction(()=>[...rec.shadowRoot.querySelectorAll('.moment img')].some(image=>image.naturalWidth>0));
  await page.locator('edge-recordings-card .big').click();await page.waitForFunction(()=>rec._dvr?.picture&&rec._dvr.frames>=4);
  await page.evaluate(()=>{window.retainedClient=rec._dvr;window.retainedSocket=rec._dvr.ws;});
  blocked=false;held.splice(0).forEach(release=>release());await page.waitForFunction(()=>rec._catalogMetrics?.restored);
  assert(await page.evaluate(()=>rec._dvr===retainedClient&&rec._dvr.ws===retainedSocket&&rec._wantPlay),'background catalog refresh restarted active DVR');
  assert.equal(catalogRequests,1);
  hold=true;await page.evaluate(()=>rec._seek(rec._files[1][1]+3,true));
  await page.locator('edge-recordings-card [data-dvr-loading]').waitFor({state:'visible'});
  assert.equal(await page.locator('edge-recordings-card .note').isVisible(),false,'preparing text was shown instead of the icon');
  hold=false;delayed.splice(0).forEach(send=>send());await page.waitForFunction(()=>rec._dvr.picture&&rec._dvr.position>=3);
  await page.locator('edge-recordings-card [data-dvr-loading]').waitFor({state:'hidden'});
  await page.evaluate(()=>{window.loaderFlashes=0;const icon=rec.shadowRoot.querySelector('[data-dvr-loading]');new MutationObserver(()=>{if(!icon.hidden)loaderFlashes++;}).observe(icon,{attributes:true,attributeFilter:['hidden']});});
  await page.waitForTimeout(1300);assert.equal(await page.evaluate(()=>loaderFlashes),0,'normal playback flashed a loader');
  await page.waitForFunction(()=>rec._dvr.frameQueue.length>=2);
  const seeks=commands.filter(c=>c.type==='seek').length;
  await page.evaluate(()=>{rec._wantPlay=false;const c=rec._dvr;c.pause();c.seek(c.request.file,c.position+c.frameDuration/1e6,{paused:true});});
  assert.equal(commands.filter(c=>c.type==='seek').length,seeks,'adjacent decoded-frame preview went back to the server');
  assert(await page.evaluate(()=>rec._dvr.picture&&rec._dvr.paused));
  denied=true;await page.reload();await page.waitForFunction(()=>{const c=document.querySelector('edge-recordings-card');return c._files.length===0&&c.shadowRoot.querySelector('.status').classList.contains('error');});
  assert.equal(await page.evaluate(async()=>Boolean(await(await edgeDashboardStore()).loadDashboard('ha:fixture-dashboard-user|camera.back|latest'))),false,'forbidden catalog stayed cached');
  assert.deepEqual(errors,[]);
  console.log(`PASS ${wk?'webkit':'chromium'}: cached dashboard/JPEG ${restoreMS} ms before held API, one catalog request, refresh retains native DVR, seek-only spinner, no steady flashes, buffered frame step and revoked-access eviction`);
 }catch(error){try{console.error(await page.evaluate(()=>{const c=document.querySelector('edge-recordings-card');return{files:c?._files?.length,status:c?.shadowRoot.querySelector('.status')?.textContent,note:c?.shadowRoot.querySelector('.note')?.textContent,player:c?._dvr?.diagnosticState()};}));}catch(_){}throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
