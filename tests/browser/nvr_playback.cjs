const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const dir=JSON.parse(fs.readFileSync(process.env.EDGE_NVR_FIXTURE_INFO || '/tmp/edge-nvr-browser-fixture.json','utf8')).recordings_dir;
(async()=>{
const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,args:['--no-sandbox']});
try{
for (const [name,width,height] of [['desktop',1440,1000],['tablet',820,1180],['phone',390,844],['small-phone',320,740],['tablet-landscape',1180,820]]) {
 const context=await browser.newContext({viewport:{width,height},isMobile:name!=='desktop',hasTouch:name!=='desktop'});
 const page=await context.newPage();page.setDefaultTimeout(30000);const errors=[];
 try {
 await page.route('**/recordings/**000007.mp4*', async route => { await new Promise(resolve=>setTimeout(resolve,400)); try { await route.continue(); } catch (error) { if (!page.isClosed() && !/already handled|has been closed|target closed|invalid interception/i.test(error.message)) throw error; } });page.on('pageerror',e=>errors.push(e.message));
 await page.goto(process.env.EDGE_NVR_TEST_URL || 'http://127.0.0.1:18088');
 await page.locator('[name="camera-0-name"]').waitFor({state:'attached'});
 if(await page.locator('nav').isHidden()) await page.locator('#menu-toggle').click();
 await page.locator('nav [data-page-target="nvr"]').click();
 await page.waitForFunction(()=>document.querySelector('[data-recording-duration]')?.textContent.includes('/ 00:00:21'));
 await page.waitForFunction(()=>{const img=document.querySelector('.recording-day-tile.active img');return img?.naturalWidth>100&&getComputedStyle(img).opacity==='1'});
 await page.waitForFunction(()=>{const img=document.querySelector('.recording-film-grid img');return img?.naturalWidth>100&&getComputedStyle(img).opacity==='1'});
 const geometry=await page.evaluate(()=>({body:document.documentElement.scrollWidth,width:innerWidth,thumbs:[...document.querySelectorAll('.recording-film-grid img')].map(i=>({width:i.getBoundingClientRect().width,height:i.getBoundingClientRect().height})),player:document.querySelector('video').getBoundingClientRect().toJSON()}));
 console.log(name,JSON.stringify(geometry));
 assert(geometry.body<=width+1,`${name} horizontal overflow ${geometry.body}`);
 for(const box of geometry.thumbs) {assert(box.width>=80);assert(box.height>=45);assert(Math.abs(box.width/box.height-16/9)<.1);}
 assert(geometry.player.width<=width&&geometry.player.height>100);
 await page.evaluate(()=>{window.savedVideo=document.querySelector('video[data-recording-player]');window.savedSrc=window.savedVideo.src;});
 await page.locator('[data-recording-speed]').selectOption('4');
 await page.locator('[data-recording-toggle]').click();
 await page.waitForFunction(()=>{const v=document.querySelector('video');const f=document.querySelector('[data-recording-transition]');return v?.dataset.recordingFileUrl.endsWith('000007.mp4')&&f&&!f.hidden},null,{timeout:15000});
 assert(await page.locator('video').evaluate(v=>!v.hasAttribute('poster')),'poster returned during playback transition');
 assert(await page.locator('[data-recording-transition]').evaluate(c=>c.width>0&&c.getContext('2d').getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4!==3&&v>0)),'last video frame was not retained');
 await page.waitForFunction(()=>document.querySelector('[data-recording-transition]')?.hidden);
 await page.waitForFunction(()=>document.querySelector('video')?.dataset.recordingFileUrl.endsWith('000014.mp4'),null,{timeout:15000});
 assert(await page.evaluate(()=>savedVideo===document.querySelector('video[data-recording-player]')),'video replaced between clips');
 await page.locator('[data-recording-toggle]').click();
 await page.locator('#refresh-nvr').click();
 await page.waitForFunction(()=>document.querySelector('#refresh-nvr')!==null);
 await page.waitForTimeout(300);
 assert(await page.evaluate(()=>savedVideo===document.querySelector('video[data-recording-player]')),'video replaced by refresh');
 assert(await page.locator('video[data-recording-player]').evaluate(v=>v.paused),'paused player resumed after refresh');
 fs.copyFileSync(`${dir}/20260728-000000.mp4`,`${dir}/20260728-000021.mp4`);
 await page.evaluate(()=>loadRecordingStatus({reason:'timer'}));
 assert((await page.locator('[data-recording-duration]').textContent()).endsWith('/ 00:00:28'));
 assert(await page.evaluate(()=>savedVideo===document.querySelector('video[data-recording-player]')),'video replaced by growing-day timer update');
 assert(await page.locator('video[data-recording-player]').evaluate(v=>v.paused),'timer resumed paused player');
 fs.unlinkSync(`${dir}/20260728-000021.mp4`);
 await page.evaluate(()=>loadRecordingStatus({reason:'timer'}));

 await page.locator('[data-recording-playback-seek]').fill('8');await page.locator('[data-recording-playback-seek]').dispatchEvent('change');
 await page.waitForFunction(()=>document.querySelector('video')?.dataset.recordingFileUrl.endsWith('000007.mp4'));
 assert(await page.locator('[data-recording-duration]').textContent().then(t=>t.endsWith('/ 00:00:21')));
 await page.locator('[data-recording-day="2026-07-27"]').click();
 await page.waitForFunction(()=>document.querySelector('[data-nvr-day]')?.dataset.nvrDay==='2026-07-27');
 await page.waitForFunction(()=>document.querySelector('video')?.dataset.recordingFileUrl.includes('20260727'));
 await page.waitForFunction(()=>{const i=document.querySelector('.recording-day-tile.active img');return i?.naturalWidth>100&&getComputedStyle(i).opacity==='1'&&!i.closest('.recording-thumb-wrap,.recording-day-thumb').classList.contains('thumb-error')});
 await page.waitForFunction(()=>{const i=document.querySelector('.recording-film-grid img');return i?.naturalWidth>100&&getComputedStyle(i).opacity==='1'&&!i.closest('.recording-thumb-wrap,.recording-day-thumb').classList.contains('thumb-error')});
 await page.locator('[data-recording-fullscreen]').click();
 // The fullscreen flag can change before the browser has applied its layout.
 await page.waitForFunction(()=>{
  const wrap=document.fullscreenElement || document.querySelector('.edge-soft-fullscreen');
  if(!wrap)return false;
  const rect=wrap.getBoundingClientRect();
  return Math.abs(rect.width-innerWidth)<2&&Math.abs(rect.height-innerHeight)<2;
 });
 const controls=await page.locator('.nvr-playback-controls').boundingBox();
 const viewport=await page.evaluate(()=>({width:innerWidth,height:innerHeight}));
 assert(controls.x>=0&&controls.y>=0&&controls.x+controls.width<=viewport.width+1&&controls.y+controls.height<=viewport.height+1,`fullscreen controls outside viewport: ${JSON.stringify({controls,viewport})}`);
 assert(await page.evaluate(()=>!document.fullscreenElement||document.fullscreenElement===document.querySelector('[data-recording-wrap]')),'native fullscreen dropped the custom playback controls');
 await page.evaluate(()=>document.fullscreenElement ? document.exitFullscreen() : exitSoftFullscreen('test'));
 await page.waitForFunction(()=>!document.fullscreenElement&&!document.querySelector('.edge-soft-fullscreen'));
 // Browsers without element fullscreen must retain controls in the in-page mode.
 await page.evaluate(()=>{
  const wrap=document.querySelector('[data-recording-wrap]'),video=wrap.querySelector('video');
  for(const method of ['requestFullscreen','webkitRequestFullscreen','msRequestFullscreen'])Object.defineProperty(wrap,method,{value:undefined,configurable:true});
  window.videoFullscreenCalls=0;
  for(const method of ['requestFullscreen','webkitEnterFullscreen'])Object.defineProperty(video,method,{value:()=>{window.videoFullscreenCalls++;throw new Error('Video-only fullscreen must not be used for NVR');},configurable:true});
 });
 await page.locator('[data-recording-fullscreen]').click();
 await page.waitForFunction(()=>{
  const wrap=document.querySelector('[data-recording-wrap].edge-soft-fullscreen');
  if(!wrap)return false;
  const rect=wrap.getBoundingClientRect();return Math.abs(rect.width-innerWidth)<2&&Math.abs(rect.height-innerHeight)<2;
 });
 const soft=await page.locator('.nvr-playback-controls').boundingBox();
 assert(soft.x>=0&&soft.y>=0&&soft.x+soft.width<=viewport.width+1&&soft.y+soft.height<=viewport.height+1,'in-page fullscreen controls outside viewport');
 assert.equal(await page.evaluate(()=>videoFullscreenCalls),0);
 await page.evaluate(()=>exitSoftFullscreen('test'));
 assert.equal(errors.length,0,errors.join('\n'));
 if (process.env.EDGE_NVR_SCREENSHOT_DIR) await page.screenshot({path:`${process.env.EDGE_NVR_SCREENSHOT_DIR}/nvr-${name}.png`,fullPage:true});
 console.log(`PASS ${name}: duration 21s, all 3 clips, stable element, pause/refresh, seek, day switch and visible 16:9 thumbnails`);
 } finally {
  await page.unrouteAll({behavior:'ignoreErrors'});
  await context.close();
 }
}
}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
