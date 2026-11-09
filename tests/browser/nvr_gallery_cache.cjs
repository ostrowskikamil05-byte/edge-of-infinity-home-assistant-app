// Real IndexedDB and JPEG decode; recordings remain on the fixture server.
const {chromium,webkit}=require('playwright');const assert=require('node:assert/strict');
async function until(page,condition){const deadline=Date.now()+30000;while(!await page.evaluate(condition)){assert(Date.now()<deadline,'asynchronous gallery condition timed out');await page.waitForTimeout(50);}}
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18092';
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 let diagnosticPage;
 try{
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:wk?'Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/HomeAssistant':'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/156 Mobile Safari/537.36'});
  const page=await context.newPage();diagnosticPage=page;page.setDefaultTimeout(30000);const errors=[],videoRequests=[];
  page.on('pageerror',e=>{
   // WebKit also emits handled fetch console diagnostics as pageerror while
   // the old document unloads. Their "name" is not a JS exception type.
   const transport=wk&&e.name.startsWith('Fetch API cannot load ')&&e.message.endsWith(' due to access control checks.');
   if(!transport)errors.push(e.message);
  });
  page.on('console',m=>{if(m.text().startsWith('EDGE_FIXTURE_UNHANDLED:'))errors.push(m.text());});
  await page.addInitScript(()=>{
   localStorage.setItem('edge-nvr-next-buffer','false');
   addEventListener('unhandledrejection',e=>console.error('EDGE_FIXTURE_UNHANDLED:'+String(e.reason)));
   window.fixtureRevokedThumbnails=new Set();const revoke=URL.revokeObjectURL.bind(URL);
   URL.revokeObjectURL=url=>{fixtureRevokedThumbnails.add(url);revoke(url);};
  });
  page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/recordings/'))videoRequests.push(r.url());});
  await page.route('**/api/edge-rtc/player/**',r=>r.fulfill({contentType:'text/html',body:'<p>Generated camera</p>'}));
  await page.goto(base);await page.waitForFunction(()=>recordingStatus[0]?.files?.length>0);
  assert.notEqual(await page.evaluate(()=>activePage),'nvr');assert.equal(videoRequests.length,0,'prefetch downloaded a video on Live');
  await until(page,async()=>Boolean(await galleryStore.loadSnapshot(recordingGalleryScope())));
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
  await page.locator('.recording-film-grid img').first().scrollIntoViewIfNeeded();
  await page.waitForFunction(()=>{const i=document.querySelector('.recording-film-grid img');return i?.src.startsWith('blob:')&&i.naturalWidth>100&&i.closest('.recording-thumb-wrap')?.classList.contains('thumb-loaded');});
  await until(page,async()=>{await galleryStore.writes;const db=await galleryStore.open();return await new Promise(resolve=>{const r=db.transaction('entries').objectStore('entries').getAll();r.onsuccess=()=>resolve(r.result.some(e=>e.kind==='thumbnail'));});});
  // A reloaded panel can display its gallery while the server history and
  // thumbnail endpoint are unavailable. Summary state is still authoritative.
  await page.route('**/api/recording/status*',r=>new URL(r.request().url()).searchParams.has('summary')?r.continue():r.abort('timedout'));
  await page.route('**/recording-thumbs/**',r=>r.abort('timedout'));
  await page.reload();try{await page.waitForFunction(()=>recordingStatus[0]?.files?.length>0);}catch(error){console.error(await page.evaluate(async()=>({scope:recordingGalleryScope(),saved:await galleryStore.loadSnapshot(recordingGalleryScope()).then(s=>s?.cameras?.map(c=>({id:c.id,index:c.index,files:c.files.length}))),current:Object.values(recordingStatus).map(c=>({id:c.id,index:c.index,files:c.files?.length})),errors:document.querySelector('#live-grid')?.textContent})));throw error;}
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
  await page.waitForFunction(()=>Boolean([...document.querySelectorAll('.recording-film-grid img')].find(i=>i.src.startsWith('blob:')&&i.naturalWidth>100)));
  // A displayed thumbnail must remain readable when WebKit reloads it after
  // layout/scroll; reclaim its URL when the actual node leaves the gallery.
  const thumbnail=await page.evaluate(async()=>{
   const image=[...document.querySelectorAll('.recording-film-grid img')].find(i=>i.src.startsWith('blob:')&&i.naturalWidth>100);
   const url=image.src,response=await fetch(url),bytes=(await response.blob()).size;
   const decoded=new Image();decoded.src=url;await decoded.decode();
   window.fixtureRetainedThumbnail=image;
   return {url,bytes,width:decoded.naturalWidth,tracked:recordingThumbnailUrls.get(image)===url};
  });
  assert(thumbnail.bytes>100&&thumbnail.width>100&&thumbnail.tracked,'displayed JPEG URL was revoked before its node was removed');
  assert.equal(await page.locator('[data-recording-day-load]:visible').count(),0,'failed refresh obscured a restored gallery');
  await page.locator('[data-recording-toggle="0"]').click();
  await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player="0"]');return !v.paused&&v.videoWidth>0&&v.currentTime>.1;});
  // Storage scope, secrets, TTL and count limits use native IDB, not a mock.
  const checks=await page.evaluate(async()=>{
   const scope=recordingGalleryScope(),saved=await galleryStore.loadSnapshot(scope);
   const noSecrets=!JSON.stringify(saved).includes('rtsp://')&&!JSON.stringify(saved).includes('password');
   const other=await galleryStore.loadSnapshot(scope+'different-server');
   await galleryStore.saveSnapshot('whitelist',[{index:0,id:'safe',files:[],password:'DO_NOT_STORE',record_rtsp:'DO_NOT_STORE'}]);
   const whitelist=JSON.stringify(await galleryStore.loadSnapshot('whitelist'));
   const blob=new Blob(['jpeg'],{type:'image/jpeg'});
   for(let i=0;i<100;i++)await galleryStore.saveThumbnail('budget','/recording-thumbs/budget/'+i+'.jpg',blob);
   const db=await galleryStore.open();
   const countRows=await new Promise(resolve=>{const r=db.transaction('entries').objectStore('entries').getAll();r.onsuccess=()=>resolve(r.result.filter(e=>e.kind==='thumbnail').length);});
   const large=new Blob([new Uint8Array(512*1024)],{type:'image/jpeg'});
   for(let i=0;i<20;i++)await galleryStore.saveThumbnail('byte-budget','/recording-thumbs/large/'+i+'.jpg',large);
   const oversize=await galleryStore.saveThumbnail('byte-budget','/recording-thumbs/oversize.jpg',new Blob([new Uint8Array(512*1024+1)],{type:'image/jpeg'}));
   const rows=await new Promise(resolve=>{const r=db.transaction('entries').objectStore('entries').getAll();r.onsuccess=()=>resolve(r.result);});
   const limited=rows.filter(r=>r.kind==='thumbnail');
   const held=new EdgeRecordingGallery();let release;const gate=new Promise(resolve=>{release=resolve;});held.open=()=>gate;
   const pending=Array.from({length:32},(_,i)=>held.put('held'+i,'thumbnail',new ArrayBuffer(512*1024),512*1024));
   const queueBound=held.queuedWrites<=8&&held.queuedBytes<=12*1024*1024;release(null);await Promise.all(pending);
   await new Promise(resolve=>{const tx=db.transaction('entries','readwrite');const row=rows.find(r=>r.key==='snapshot|whitelist');row.updated=Date.now()-row.ttl-1;tx.objectStore('entries').put(row);tx.oncomplete=resolve;});
   return {noSecrets,other,whitelist,count:countRows,oversize,queueBound:queueBound&&held.queuedWrites===0&&held.queuedBytes===0,bytes:limited.reduce((n,r)=>n+r.bytes,0),expired:await galleryStore.loadSnapshot('whitelist')};
  });
  assert(checks.noSecrets);assert.equal(checks.other,null);assert(!checks.whitelist.includes('DO_NOT_STORE'));assert(checks.count<=96&&checks.bytes<=8*1024*1024);assert(checks.queueBound);assert.equal(checks.oversize,false);assert.equal(checks.expired,null);
  await page.evaluate(()=>fixtureRetainedThumbnail.remove());
  await page.waitForFunction(url=>!recordingThumbnailUrls.has(fixtureRetainedThumbnail)&&fixtureRevokedThumbnails.has(url),thumbnail.url);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
  console.log(`PASS ${wk?'webkit':'chromium'}: background metadata only, persistent gallery/JPEG before network, actual playback, scope/secret/expiry/storage limits`);
  await page.unrouteAll({behavior:'ignoreErrors'});await context.close();
 }catch(error){console.error('Gallery test failed',error);try{console.error('Native gallery state',await diagnosticPage.evaluate(()=>({video:recordingVideoDiagnostics(document.querySelector('video[data-recording-player]')),activePage,phone:phonePlayback()})));}catch(_){}throw error;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
