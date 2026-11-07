const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  for(const width of [1440,820,390,320]){
   const context=await browser.newContext({viewport:{width,height:1000},isMobile:width<=820,hasTouch:width<=820,permissions:['clipboard-read','clipboard-write']});
   const page=await context.newPage();page.setDefaultTimeout(15000);const errors=[];let previews=0;
   page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(new URL(r.url()).pathname.endsWith('/api/detection/preview'))previews++;});
   const nav=async target=>{if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator(`nav [data-page-target="${target}"]`).click();};
   const save=async()=>{await page.locator('#save-config').click();await page.waitForFunction(()=>document.getElementById('save-state').textContent.startsWith('Saved and verified.'));};
   await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088');await page.locator('[data-camera-form]').waitFor({state:'attached'});
   await nav('camera-settings');
   const toggleBox=await page.locator('[name="camera-0-local-detection-enabled"]').locator('..').boundingBox();
   assert(toggleBox.width>=100&&toggleBox.x>=0&&toggleBox.x+toggleBox.width<=width,'detection toggle label is clipped');
   await page.locator('[data-zone-editor] summary').click();
   await page.locator('[data-zone-action="full"]').click();
   assert.equal(previews,0,'opening/adjusting a zone must not auto-decode camera frames');
   assert(await page.locator('[data-zone-action="draw"]').isDisabled());
   await page.locator('[data-zone-action="preview"]').click();
   await page.waitForFunction(()=>document.querySelector('[data-zone-image]')?.naturalWidth===416&&!document.querySelector('[data-zone-image]').hidden);
   assert.equal(await page.locator('[data-zone-image]').evaluate(image=>image.naturalHeight),234);
   assert.equal(previews,1);assert(await page.locator('[data-zone-placeholder]').isHidden());
   await page.locator('[data-zone-action="draw"]').click();
   await page.locator('[data-zone-stage]').scrollIntoViewIfNeeded();
   const box=await page.locator('[data-zone-stage] svg').boundingBox();
   const start={x:box.x+box.width*.2,y:box.y+box.height*.3},end={x:box.x+box.width*.8,y:box.y+box.height*.9};
   if(width<=820){
    const cdp=await context.newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{...start,id:0}]});
    for(let step=1;step<=5;step++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:start.x+(end.x-start.x)*step/5,y:start.y+(end.y-start.y)*step/5,id:0}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await cdp.detach();
   }else{await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(end.x,end.y,{steps:5});await page.mouse.up();}
   const bounds=await Promise.all(['left','top','right','bottom'].map(name=>page.locator(`[name="camera-0-zone-${name}"]`).inputValue()));
   bounds.forEach((value,i)=>assert(Math.abs(Number(value)-[20,30,80,90][i])<.3,`incorrect drawn bounds ${bounds}`));
   assert(await page.locator('[name="camera-0-zone-enabled"]').isChecked());
   assert((await page.locator('[data-page="camera-settings"] [data-unsaved-indicator]').textContent()).includes('Unsaved'));
   await page.locator('[data-zone-action="draw"]').click();await page.locator('[data-zone-stage]').scrollIntoViewIfNeeded();
   const small=await page.locator('[data-zone-stage] svg').boundingBox();await page.mouse.click(small.x+small.width*.5,small.y+small.height*.5);
   assert.deepEqual(await Promise.all(['left','top','right','bottom'].map(name=>page.locator(`[name="camera-0-zone-${name}"]`).inputValue())),bounds,'a tap erased the previous zone');
   if(process.env.EDGE_ZONE_SCREENSHOT_DIR)await page.screenshot({path:`${process.env.EDGE_ZONE_SCREENSHOT_DIR}/zone-${width}.png`,fullPage:true});
   await save();
   const stored=await page.evaluate(()=>fetch('api/config').then(r=>r.json()));
   assert(stored.cameras[0].detection_zone_enabled);assert(Math.abs(stored.cameras[0].detection_zone_left-.2)<.003);
   assert.equal(previews,1,'saving must not request another preview');
   await page.reload();await page.locator('[data-camera-form]').waitFor({state:'attached'});await nav('camera-settings');
   assert(await page.locator('[name="camera-0-zone-enabled"]').isChecked());
   await page.locator('[data-zone-editor] summary').click();assert(Math.abs(Number(await page.locator('[name="camera-0-zone-right"]').inputValue())-80)<.3);
   await page.locator('[name="camera-0-zone-left"]').fill('85');await page.locator('#save-config').click();
   await page.waitForFunction(()=>document.getElementById('save-state').textContent.includes('Strefa AI:'));
   const unchanged=await page.evaluate(()=>fetch('api/config').then(r=>r.json()));assert.deepEqual(unchanged,stored);
   assert.equal(await page.locator('[name="camera-0-zone-left"]').inputValue(),'85','failed save discarded the edited form');
   await page.locator('[name="camera-0-zone-left"]').fill('20');await save();
   await nav('events');await page.locator('#event-notify-camera option').waitFor({state:'attached'});
   await page.locator('#event-notifications summary').click();await page.locator('#event-notify-service').fill('notify.mobile_app_phone');
   await page.locator('#event-notify-source').selectOption('zone');
   let yaml=await page.locator('#event-notification-yaml').inputValue();
   assert(yaml.includes('source: local')&&yaml.includes('zone_id: local_zone'));
   assert(!/rtsp:|secret|192\.0\.2/.test(yaml));await page.locator('#event-copy-notification').click();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),yaml);
   await page.locator('#event-notify-source').selectOption('camera');yaml=await page.locator('#event-notification-yaml').inputValue();assert(yaml.includes('source: camera')&&!yaml.includes('zone_id:'));
   await page.locator('#event-notify-source').selectOption('local');yaml=await page.locator('#event-notification-yaml').inputValue();assert(yaml.includes('source: local')&&!yaml.includes('zone_id:'));
   await nav('camera-settings');await page.locator('[name="camera-0-zone-enabled"]').uncheck();await save();
   await nav('events');await page.locator('#event-notify-source').selectOption('zone');
   assert(await page.locator('#event-copy-notification').isDisabled());assert.equal(await page.locator('#event-notification-yaml').inputValue(),'');
   assert((await page.locator('#event-notification-status').textContent()).includes('włącz i zapisz'));
   assert.equal(await page.evaluate(()=>fetch('api/detection/preview?camera=unknown').then(r=>r.status)),404);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
   console.log(`PASS zones ${width}px: explicit preview, ${width<=820?'touch':'mouse'} drawing, cancelled tap, persistence/reload, rejected save and source/zone notification filters`);
   await context.close();
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
