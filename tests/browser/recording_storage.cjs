const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const fixture=JSON.parse(fs.readFileSync(process.env.EDGE_NVR_FIXTURE_INFO||'/tmp/edge-nvr-browser-fixture.json','utf8'));
const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  for(const width of [1440,820,390,320]){
   const page=await browser.newPage({viewport:{width,height:1100},hasTouch:width<1000});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   const originals=fs.readdirSync(fixture.recordings_dir).filter(name=>name.endsWith('.mp4')).map(name=>path.join(fixture.recordings_dir,name));
   const before=originals.map(hash);
   await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088');
   await page.locator('[name="camera-0-name"]').waitFor({state:'attached'});
   async function open(name){if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator(`nav [data-page-target="${name}"]`).click();}
   await open('edge-settings');
   assert.equal(await page.locator('[name="nvr-browser-playback"]').inputValue(),'copy');
   assert.equal(await page.locator('[name="nvr-playback-cache-mb"]').inputValue(),'0');
   await open('nvr');
   await page.waitForFunction(()=>document.querySelector('[data-recording-duration]')?.textContent.endsWith('/ 00:00:21'));
   await open('edge-settings');
   const copy=path.join(fixture.recording_cache_dir,'front_0','2026-07-27','timeline.mp4');
   fs.mkdirSync(path.dirname(copy),{recursive:true});fs.writeFileSync(copy,Buffer.alloc(2*1024*1024,7));
   fs.writeFileSync(path.join(path.dirname(copy),'timeline.json'),'{}');
   const orphan=path.join(fixture.thumbnail_dir,'front_0','20260726-000000.mp4.jpg');
   fs.mkdirSync(path.dirname(orphan),{recursive:true});fs.writeFileSync(orphan,Buffer.alloc(4096,8));
   await page.locator('[data-cleanup-recording-cache]').click();
   await page.waitForFunction(()=>document.querySelector('[data-storage-cleanup-result]')?.textContent.startsWith('Zwolniono'));
   assert(!fs.existsSync(copy),'duplicate video cache survived zero budget');
   assert(!fs.existsSync(orphan),'orphan thumbnail survived cleanup');
   assert.deepEqual(originals.map(hash),before,'cleanup changed original footage');
   const status=await page.evaluate(()=>fetch('api/recording/status').then(r=>r.json()));
   assert.equal(status.storage.playback_cache_bytes,0);
   assert.equal(status.cameras[0].playback_cache.enabled,false);
   assert(status.storage.recordings_bytes>0&&status.cameras[0].bytes_per_day_estimate>0);
   await page.waitForFunction(()=>document.querySelector('#edge-settings-form [data-recording-disk-usage]')?.textContent.includes('Szacunek'));
   await open('nvr');
   await page.waitForFunction(()=>document.querySelector('[data-recording-duration]')?.textContent.endsWith('/ 00:00:21'));
   assert.equal(await page.locator('video').getAttribute('data-recording-playback-mode'),'server_file_sequence');
   await page.locator('[data-recording-playback-seek]').fill('15');await page.locator('[data-recording-playback-seek]').dispatchEvent('change');
   await page.waitForFunction(()=>document.querySelector('video')?.dataset.recordingFileUrl.endsWith('000014.mp4'));
   await page.getByRole('button',{name:'Previous day',exact:true}).click();
   await page.waitForFunction(()=>document.querySelector('video')?.dataset.recordingFileUrl.includes('20260727'));
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'disk-usage view overflows');
   assert.deepEqual(errors,[]);
   console.log(`PASS ${width}px: cleanup frees duplicate MP4/orphan JPEG, original hashes unchanged, visible usage/estimates and full-day seek without playback copies`);
   await page.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
