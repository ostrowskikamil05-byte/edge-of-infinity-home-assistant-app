// Chromium emulates phone loading restrictions; physical WebKit/Android QA
// remains necessary. Media validity and HEVC pixel identity are tested in Python.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  for(const [name,userAgent] of [
   ['iPhone','Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Home Assistant/2026.11'],
   ['Android','Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 Chrome/142.0.0.0 Mobile Safari/537.36']
  ]){
   const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true,userAgent});
   await context.addInitScript(()=>{
    const proto=HTMLMediaElement.prototype,ready=Object.getOwnPropertyDescriptor(proto,'readyState').get,play=proto.play,load=proto.load;
    Object.defineProperty(proto,'readyState',{configurable:true,get(){return this.matches('[data-recording-player]')&&!this.phoneUnlocked?Math.min(1,ready.call(this)):ready.call(this);}});
    proto.play=function(){this.phoneUnlocked=true;this.phonePlayCalls=(this.phonePlayCalls||0)+1;return play.call(this);};
    proto.load=function(){this.phoneUnlocked=false;return load.call(this);};
    // Some mobile surfaces defer callbacks while covered by the held canvas.
    HTMLVideoElement.prototype.requestVideoFrameCallback=()=>1;
   });
   const page=await context.newPage();page.setDefaultTimeout(20000);const errors=[],responses=[];
   page.on('pageerror',error=>errors.push(error.message));page.on('response',response=>{if(response.url().includes('/recordings/'))responses.push(response);});
   await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088');
   await page.locator('[data-camera-form]').waitFor({state:'attached'});if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
   const video=page.locator('video[data-recording-player]'),toggle=page.locator('[data-recording-toggle]');
   await page.waitForFunction(()=>document.querySelector('video[data-recording-player]')?.readyState>=1);
   assert.equal(await video.evaluate(v=>v.readyState),1);
   await toggle.click();await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player]');return !v.paused&&v.readyState>=2&&v.currentTime>.1;});
   assert(await video.evaluate(v=>v.phonePlayCalls>0),'Play waited for a frame that requires Play to load');
   await video.evaluate(v=>pauseRecording(v));
   const slider=page.locator('[data-recording-playback-seek]');
   await slider.evaluate(s=>{s.value='9';s.dispatchEvent(new Event('input',{bubbles:true}));s.dispatchEvent(new Event('change',{bubbles:true}));});
   await page.waitForFunction(()=>document.querySelector('video[data-recording-player]').readyState<=1);
   await toggle.click();
   await page.waitForFunction(()=>{const v=document.querySelector('video[data-recording-player]');return !recordingScrubs.size&&!v.paused&&recordingPlaybackPosition(v)>=9&&document.querySelector('[data-recording-transition]').hidden;});
   await video.evaluate(v=>pauseRecording(v));
   assert(responses.length&&responses.every(r=>new URL(r.url()).searchParams.get('compat')==='1'),'phone bypassed normalized MP4');
   assert(responses.some(r=>r.status()===206),'phone playback lost byte-range seeking');
   const normalized=await page.evaluate(()=>mediaUrlWithTimeFragment(panelPath('api/alerts/1/clip.mp4'),2));
   assert(new URL(normalized).searchParams.get('compat')==='1'&&normalized.endsWith('#t=2.000'));
   assert.deepEqual(errors,[]);console.log(`PASS ${name}: phone container/range requests, Play before first decoded frame, resume during seek, no occlusion deadlock`);
   await context.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
