// Real browser decoders, two codecs, AAC, camera switching and inline markers.
// Run against EDGE_TEST_MULTI_CAMERA=1 fixture; no readyState/play mocks.
const {chromium,webkit}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const engine=process.env.EDGE_TEST_BROWSER||'chromium',isWebKit=engine==='webkit';
 const browser=await (isWebKit?webkit:chromium).launch({headless:true,executablePath:(isWebKit?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:isWebKit?[]:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:isWebKit?'Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148':'Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/142.0.0.0 Mobile Safari/537.36'});
  const page=await context.newPage();page.setDefaultTimeout(25000);const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/edge-rtc/player/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><p>Shared-source live iframe lifecycle fixture</p>'}));
  await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18091');
  await page.locator('#camera-grid iframe').first().waitFor();
  const nav=async name=>{if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator(`nav [data-page-target="${name}"]`).click();};
  await nav('nvr');await page.locator('video[data-recording-player="1"]').waitFor({state:'attached'});
  assert(await page.locator('#camera-grid iframe').evaluateAll(frames=>frames.every(frame=>frame.getAttribute('src')==='about:blank')),'hidden live decoders remain attached');
  const first=page.locator('video[data-recording-player="0"]'),second=page.locator('video[data-recording-player="1"]');
  const ready=async index=>page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`);return v.readyState>=2&&!v.seeking;},index);
  const play=async index=>{await page.locator(`[data-recording-toggle="${index}"]`).click();await page.waitForFunction(index=>{const v=document.querySelector(`video[data-recording-player="${index}"]`);return !v.paused&&v.currentTime>.1&&v.readyState>=2;},index);};
  const pixels=async video=>video.evaluate(v=>{const c=document.createElement('canvas');c.width=160;c.height=90;const g=c.getContext('2d');g.drawImage(v,0,0,160,90);const d=g.getImageData(0,0,160,90).data;let bright=0;for(let i=0;i<d.length;i+=4)if(d[i]+d[i+1]+d[i+2]>90)bright++;return{bright,image:c.toDataURL()};});
  const seek=async(index,position)=>{await page.locator(`[data-recording-playback-seek="${index}"]`).evaluate((s,p)=>{s.value=String(p);s.dispatchEvent(new Event('input',{bubbles:true}));s.dispatchEvent(new Event('change',{bubbles:true}));},position);await page.waitForFunction(({index,position})=>{const v=document.querySelector(`video[data-recording-player="${index}"]`);return !recordingScrubs.size&&v.readyState>=2&&!v.seeking&&Math.abs(recordingPlaybackPosition(v)-position)<.25;},{index,position});};
  await ready('0');assert.equal(await second.getAttribute('src'),null,'inactive phone camera downloaded/decoded video');
  if(!await first.evaluate(v=>v.videoWidth>0)){
   // Linux Chromium can play AAC while silently ignoring its unsupported HEVC
   // track. Verify the explicit warning, then test real H264 Android playback.
   assert(!isWebKit,'WebKit HEVC test requires its real decoder');
   await page.waitForFunction(()=>document.querySelector('[data-recording-playback-message="0"]').textContent.includes('H.265'));
   assert(await first.evaluate(v=>v.paused),'unsupported video continued as black audio-only playback');
   await play('1');assert((await pixels(second)).bright>2000);await second.evaluate(v=>pauseRecording(v));
   await seek('1',2);const a=await pixels(second);await seek('1',10);assert.notEqual(a.image,(await pixels(second)).image);
   assert.equal(await first.getAttribute('src'),null,'unsupported inactive decoder was retained');
   await nav('home');assert(await page.locator('video[data-recording-player]').evaluateAll(videos=>videos.every(v=>v.paused&&!v.getAttribute('src'))));
   assert.deepEqual(errors,[]);console.log('PASS chromium: unavailable HEVC is reported instead of black audio-only playback; native H264/AAC pixels, seek and phone decoder lifecycle verified');
   await context.close();return;
  }
  await play('0');assert((await pixels(first)).bright>2000,'HEVC video is black');await first.evaluate(v=>pauseRecording(v));
  await seek('0',2);const a=await pixels(first);await seek('0',10);const b=await pixels(first);assert(a.image!==b.image,'seek did not change actual decoded pixels');
  // A canvas readback failure must not leave the native picture occluded.
  await first.evaluate(v=>{const c=v.closest('[data-recording-wrap]').querySelector('[data-recording-transition]');c.width=640;c.height=360;c.hidden=false;c.getContext=()=>{throw Error('simulated WebKit readback rejection');};releaseRecordingFrame(v);});
  await page.waitForFunction(()=>document.querySelector('[data-recording-transition]').hidden);
  const point=page.locator('[data-alert-point="0"][data-alert-ids="3"]');await point.waitFor();
  assert.equal(await point.getAttribute('data-passed'),'true');
  const green=await point.evaluate(n=>getComputedStyle(n,'::after').backgroundColor);
  await seek('0',2);assert.equal(await point.getAttribute('data-passed'),'false');
  const gray=await point.evaluate(n=>getComputedStyle(n,'::after').backgroundColor);assert.notEqual(gray,green);
  const alignment=await point.evaluate(n=>{const p=n.getBoundingClientRect(),s=n.closest('.nvr-seek-shell').querySelector('input').getBoundingClientRect();return Math.abs(p.y+p.height/2-(s.y+s.height/2));});assert(alignment<1,'alert point is above the seek track');
  await point.click();await page.locator('[data-alert-detail="0"] img').waitFor();
  await play('1');assert((await pixels(second)).bright>2000,'H264 video is black');assert.equal(await first.getAttribute('src'),null,'previous camera retained its decoder');
  await second.evaluate(v=>pauseRecording(v));await seek('1',16);assert((await pixels(second)).bright>2000);
  await play('0');assert((await pixels(first)).bright>2000,'camera failed after codec switch back to HEVC');
  const source=await first.evaluate(v=>v.dataset.recordingSrc||v.src),response=await context.request.get(new URL(source,page.url()).href,{headers:{Range:'bytes=-16'}});
  assert.equal(response.status(),206);assert.equal((await response.body()).length,16);assert.match(response.headers()['content-range'],/bytes \d+-\d+\/\d+/);
  await nav('home');assert(await page.locator('video[data-recording-player]').evaluateAll(videos=>videos.every(v=>v.paused&&!v.getAttribute('src'))));
  await page.waitForFunction(()=>[...document.querySelectorAll('#camera-grid iframe')].every(f=>f.getAttribute('src')!=='about:blank'));
  assert.deepEqual(errors,[]);console.log(`PASS ${engine}: real HEVC/H264 + AAC pixels, pause/seek, inline gray/green alert points, decoder isolation, canvas readback recovery and HTTP suffix ranges`);
  await context.close();
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
