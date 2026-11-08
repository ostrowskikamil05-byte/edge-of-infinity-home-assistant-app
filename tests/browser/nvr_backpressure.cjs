const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  for(const width of [1440,820,390,320]){
   const context=await browser.newContext({viewport:{width,height:1000},isMobile:width<1000,hasTouch:width<1000});
   const page=await context.newPage();page.setDefaultTimeout(25000);const errors=[],recordings=[],attempts=new Map();
   page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.url().includes('/recordings/'))recordings.push(request.url());});
   await page.route('**/recording-thumbs/**',route=>{
    if(route.request().resourceType()!=='fetch')return route.continue();
    const key=route.request().url(),count=(attempts.get(key)||0)+1;attempts.set(key,count);
    return count<=2?route.fulfill({status:429,headers:{'Retry-After':'1'},body:'thumbnail_generation_busy'}):route.continue();
   });
   await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088');await page.locator('[data-camera-form]').waitFor({state:'attached'});
   const nav=async target=>{if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator(`nav [data-page-target="${target}"]`).click();};
   await nav('nvr');
   await page.waitForFunction(()=>{const images=[...document.querySelectorAll('.recording-film-grid img')];return images.length>0&&images.every(image=>image.closest('.recording-thumb-wrap')?.classList.contains('thumb-loaded'));});
   assert([...attempts.values()].some(count=>count>=3),'busy JPEGs were not retried');
   assert(await page.locator('.recording-film-grid img').evaluateAll(images=>images.every(image=>image.dataset.recordingThumbBackend==='jpeg')));
   assert(recordings.every(url=>url.includes('000000.mp4')),'a busy JPEG triggered full-video preview downloads');
   await page.route('**/api/detection',route=>route.fulfill({json:{model:'NanoDet',model_installed:true,max_fps:1,analysis_ms:100,cameras:[{id:'front',name:'Front',camera_status:'disabled',local_status:'running',supported_kinds:['person'],states:{person:false},decoder:{backend:'cpu_keyframes',fallback_reason:'cpu_overload'}}]}}));
   await nav('events');await page.locator('#detection-cameras .notice.warning').waitFor();
   assert((await page.locator('#detection-cameras .notice.warning').textContent()).includes('mogą zostać pominięte'),'resource-saving AI limitation is hidden');
   await nav('edge-settings');await page.locator('[name="detection-decoder"]').evaluate(node=>node.closest('details').open=true);await page.locator('[name="detection-decoder"]').selectOption('keyframes');
   await page.locator('#save-edge-settings').click();await page.waitForFunction(()=>document.getElementById('edge-save-state').textContent.startsWith('Saved and verified.'));
   const config=await page.evaluate(()=>fetch('api/config').then(response=>response.json()));assert.equal(config.detection.decoder,'keyframes');
   assert.deepEqual(errors,[]);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   console.log(`PASS ${width}px: busy-thumbnail backoff without video downloads, completed JPEGs and explicit reduced-AI warning/decoder persistence`);
   await page.unrouteAll({behavior:'ignoreErrors'});await context.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
