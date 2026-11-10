// Native replacement for the removed HTML MP4 navigation/seek path.
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
try{for(const [width,height] of [[1440,1000],[820,1180],[390,844],[320,740]]){
 const context=await browser.newContext({viewport:{width,height},hasTouch:width<1000,isMobile:width<1000}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(15000);const mp4=[];page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/recordings/'))mp4.push(r.url());});if(width===1440)await page.route('**/recording-thumbs/**',r=>r.abort('failed'));
 await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088');await page.locator('[name="camera-0-name"]').waitFor({state:'attached'});
 if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
 await page.waitForFunction(()=>document.querySelector('[data-nvr-day]')?.dataset.nvrDay==='2026-07-28');
 let held;await page.route('**/api/recording/status*',r=>{if(!held){held=r;return;}return r.continue();});await page.evaluate(()=>{void loadRecordingStatus({reason:'timer'});});await page.waitForFunction(()=>nvrLoading);
 await page.getByRole('button',{name:'Previous day',exact:true}).click();await page.waitForFunction(()=>document.querySelector('[data-nvr-day]')?.dataset.nvrDay==='2026-07-27');
 await page.waitForFunction(()=>archivePlayers.get('0')?.client?.picture&&archivePlayers.get('0').client.request.file.includes('20260727'));
 await held.abort().catch(()=>{});await page.unroute('**/api/recording/status*');
 for(const day of ['2026-07-28','2026-07-27','2026-07-28'])await page.locator(`[data-recording-day="${day}"]`).click();
 await page.waitForFunction(()=>!nvrLoading&&recordingStatus[0]?.selected_day==='2026-07-28'&&archivePlayers.get('0')?.client?.picture);
 await page.evaluate(()=>{window.preview=archivePlayers.get('0');preview.pause();window.previewSocket=preview.client.ws;});
 const host=page.locator('[data-dvr-host="0"]'),slider=host.locator('[data-dvr-seek]');
 const frame=async value=>{await slider.evaluate((s,value)=>{s.value=String(value);s.dispatchEvent(new Event('input',{bubbles:true}));s.dispatchEvent(new Event('change',{bubbles:true}));},value);await page.waitForFunction(value=>preview.client.picture&&preview.client.paused&&Math.abs(preview.position-value)<.15,value);return host.locator('canvas').evaluate(c=>{const small=document.createElement('canvas');small.width=160;small.height=90;small.getContext('2d').drawImage(c,0,0,160,90);return small.toDataURL();});};
 const first=await frame(0),next=await frame(5);assert.notEqual(first,next);assert.equal(await frame(0),first);assert(await page.evaluate(()=>preview===archivePlayers.get('0')&&preview.client.ws===previewSocket&&preview.client.paused));
 assert.equal(await page.locator('[data-recording-scrub-preview]').count(),0);
 await host.locator('[data-dvr-action="fullscreen"]').click();await page.waitForFunction(()=>document.fullscreenElement||document.querySelector('.edge-soft-fullscreen'));await frame(5);
 assert(await slider.evaluate(s=>(document.fullscreenElement||document.querySelector('.edge-soft-fullscreen')).contains(s)));await page.evaluate(()=>document.fullscreenElement?document.exitFullscreen():exitSoftFullscreen('test'));
 assert.equal(mp4.length,0,'JPEG failure caused a hidden MP4 decoder/download');assert.deepEqual(errors,[]);await page.unrouteAll({behavior:'ignoreErrors'});await context.close();console.log(`PASS native ${width}px: hung refresh preemption, rapid day selection, actual forward/back pictures, persistent paused socket, fullscreen seek`);
}}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
