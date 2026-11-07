const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  for(const [name,width,height] of [['desktop',1440,1000],['tablet',820,1180],['phone',390,844],['small-phone',320,740]]){
   const context=await browser.newContext({viewport:{width,height},hasTouch:width<1000,isMobile:width<1000});
   const page=await context.newPage();page.setDefaultTimeout(20000);const errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088');
   await page.locator('[name="camera-0-name"]').waitFor({state:'attached'});
   if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();
   await page.locator('nav [data-page-target="nvr"]').click();
   await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
   const slider=page.locator('[data-recording-playback-seek]'),wrap=page.locator('[data-recording-wrap]');
   assert.equal(await wrap.locator('input[type="range"]').count(),1);
   assert.equal(await page.locator('[data-recording-day-seek]').count(),0);
   await page.evaluate(()=>{window.originalScrubVideo=document.querySelector('video');originalScrubVideo.pause();});
   async function frameAt(position){
    try{await page.waitForFunction(position=>{const v=document.querySelector('video');const hold=document.querySelector('[data-recording-transition]');return v?.readyState>=2&&!v.seeking&&Math.abs(recordingPlaybackPosition(v)-position)<.4&&(!hold||hold.hidden);},position);}
    catch(error){console.error(name,position,await page.evaluate(()=>{const v=document.querySelector('video'),s=document.querySelector('[data-recording-playback-seek]'),state=recordingScrubs.get('0');return{position:recordingPlaybackPosition(v),value:s.value,rect:s.getBoundingClientRect().toJSON(),style:{padding:getComputedStyle(s).padding,width:getComputedStyle(s).width},hold:document.querySelector('[data-recording-transition]').hidden,seeking:v.seeking,ready:v.readyState,state:state&&{active:state.active,desired:state.desired,applied:state.applied,finishing:state.finishing}};}));throw error;}
   }
   async function setPosition(position){
    await slider.evaluate((node,value)=>{node.value=String(value);node.dispatchEvent(new Event('input',{bubbles:true}));node.dispatchEvent(new Event('change',{bubbles:true}));},position);
    await frameAt(position);await page.waitForFunction(()=>recordingScrubs.size===0);
   }
   async function pixels(){return page.locator('video').evaluate(video=>{const c=document.createElement('canvas');c.width=160;c.height=90;c.getContext('2d').drawImage(video,0,0,160,90);return c.toDataURL();});}
   await setPosition(1);const before=await pixels();
   await slider.scrollIntoViewIfNeeded();
   let rect=await slider.boundingBox();
   const x=value=>rect.x+8+(rect.width-16)*value/20.9,y=()=>rect.y+rect.height/2;
   await page.mouse.move(x(1),y());await page.mouse.down();
   await page.mouse.move(x(4.4),y(),{steps:8});await frameAt(4.4);
   assert(await page.evaluate(()=>recordingScrubs.get('0')?.active&&document.querySelector('video').paused),'drag did not preview while held');
   assert.notEqual(await pixels(),before,'main video pixels did not change during drag');
   await page.mouse.move(x(9.2),y(),{steps:8});await frameAt(9.2);
   assert(await page.evaluate(()=>document.querySelector('video')===originalScrubVideo),'drag replaced the video element');
   await page.mouse.move(x(2.1),y(),{steps:8});await frameAt(2.1);await page.mouse.up();
   await page.waitForFunction(()=>recordingScrubs.size===0);
   assert(await page.locator('video').evaluate(v=>v.paused),'paused video resumed after drag');
   // A held keyboard key also previews; Escape restores the starting point.
   await slider.focus();await page.keyboard.down('ArrowRight');await page.waitForTimeout(120);
   assert(await page.evaluate(()=>recordingScrubs.get('0')?.active));await page.keyboard.up('ArrowRight');
   await page.waitForFunction(()=>recordingScrubs.size===0);
   const original=await page.evaluate(()=>recordingPlaybackPosition(document.querySelector('video')));
   await page.keyboard.down('End');await frameAt(20.9);await page.keyboard.press('Escape');await page.keyboard.up('End');
   await frameAt(original);await page.waitForFunction(()=>recordingScrubs.size===0);
   // Resume only a video that was playing before the gesture.
   await page.locator('[data-recording-toggle]').click();await page.waitForFunction(()=>!document.querySelector('video').paused);
   await slider.scrollIntoViewIfNeeded();rect=await slider.boundingBox();
   await page.mouse.move(x(original),y());await page.mouse.down();await page.mouse.move(x(11.3),y(),{steps:8});await frameAt(11.3);
   assert(await page.locator('video').evaluate(v=>v.paused));await page.mouse.up();
   await page.waitForFunction(()=>recordingScrubs.size===0&&!document.querySelector('video').paused);
   await page.locator('video').evaluate(v=>v.pause());
   // Real touch events drive the same range/decoder path, not synthetic inputs.
   if(width<1000){
    await setPosition(1);rect=await slider.boundingBox();const cdp=await context.newCDPSession(page);
    const point=value=>({x:x(value),y:y(),radiusX:2,radiusY:2,force:1,id:0});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[point(1)]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[point(5.2)]});await frameAt(5.2);
    assert(await page.evaluate(()=>recordingScrubs.get('0')?.active));
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    await page.waitForFunction(()=>recordingScrubs.size===0);assert(await page.locator('video').evaluate(v=>v.paused));await cdp.detach();
   }
   // The one seek bar stays inside the picture, including both fullscreen modes.
   const geometry=await page.evaluate(()=>{const v=document.querySelector('video').getBoundingClientRect(),c=document.querySelector('.nvr-playback-controls').getBoundingClientRect();return{v:v.toJSON(),c:c.toJSON(),overflow:document.documentElement.scrollWidth>innerWidth+1};});
   assert(!geometry.overflow);assert(geometry.c.y>=geometry.v.y&&geometry.c.bottom<=geometry.v.bottom+1,'controls rendered below the video');
   if(process.env.EDGE_NVR_SCREENSHOT_DIR)await wrap.screenshot({path:`${process.env.EDGE_NVR_SCREENSHOT_DIR}/scrub-${name}.png`});
   await page.locator('[data-recording-fullscreen]').click();
   await page.waitForFunction(()=>{const w=document.fullscreenElement||document.querySelector('.edge-soft-fullscreen');return w&&Math.abs(w.getBoundingClientRect().height-innerHeight)<2;});
   await setPosition(3.5);
   assert(await page.evaluate(()=>{const f=document.fullscreenElement||document.querySelector('.edge-soft-fullscreen'),s=document.querySelector('[data-recording-playback-seek]');return f.contains(s); }));
   await page.evaluate(()=>document.fullscreenElement?document.exitFullscreen():exitSoftFullscreen('test'));
   // A superseded slow clip load must settle on the last dragged position.
   await page.route('**/recordings/**000007.mp4*',async route=>{await new Promise(resolve=>setTimeout(resolve,1500));try{await route.continue();}catch(_){} });
   rect=await slider.boundingBox();await page.mouse.move(x(3.5),y());await page.mouse.down();
   await page.mouse.move(x(8),y());await page.waitForTimeout(50);await page.mouse.move(x(16),y());await page.mouse.move(x(3),y());await page.mouse.up();
   await frameAt(3);await page.waitForFunction(()=>recordingScrubs.size===0);
   assert(await page.locator('video').evaluate(v=>v.paused));
   assert.deepEqual(errors,[]);console.log(`PASS ${name}: one in-picture bar, real frames while held, forward/back across clips, pause/resume, keyboard cancel, touch, fullscreen and latest slow seek`);
   await page.unrouteAll({behavior:'ignoreErrors'});await context.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
