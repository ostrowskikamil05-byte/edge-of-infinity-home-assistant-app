const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,args:['--no-sandbox']});
 try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(process.env.EDGE_NVR_TEST_URL || 'http://127.0.0.1:18088');
 await page.locator('[name="camera-0-vendor"]').waitFor({state:'attached'});
 await page.locator('nav [data-page-target="camera-settings"]').click();
 await page.locator('[name="camera-0-vendor"]').selectOption('reolink');
 await page.locator('[name="camera-0-host"]').fill('192.0.2.42');
 await page.locator('[name="camera-0-username"]').fill('test-user');
 await page.locator('[name="camera-0-password"]').fill('test@new:#/?');
 await page.locator('#save-config').click();
 await page.waitForFunction(()=>document.querySelector('#save-state')?.textContent.includes('Home Assistant add-on options updated.'));
 let saved=await page.evaluate(()=>fetch('api/config').then(r=>r.json()));
 assert.equal(saved.cameras[0].vendor,'reolink');
 let url=new URL(saved.cameras[0].rtsp_main);assert.equal(url.hostname,'192.0.2.42');assert.equal(url.pathname,'/h264Preview_01_main');assert.equal(decodeURIComponent(url.password),'test@new:#/?');
 assert.equal(new URL(saved.cameras[0].onvif_url).hostname,'192.0.2.42');
 await page.reload();await page.locator('[name="camera-0-vendor"]').waitFor({state:'attached'});
 assert.equal(await page.locator('[name="camera-0-vendor"]').inputValue(),'reolink');
 await page.route('**/api/camera-autoconfig',route=>route.fulfill({json:{ok:true,camera:{...saved.cameras[0],rtsp_main:'rtsp://test-user:test@192.0.2.42:8554/discovered/main',rtsp_sub:'rtsp://test-user:test@192.0.2.42:8554/discovered/sub'},device:{},streams:{},channels:[],recommendations:[],sections:{onvif:{ok:true}}}}));
 await page.locator('nav [data-page-target="camera-settings"]').click();
 await page.locator('[data-autoconfig="0"]').click();
 await page.waitForFunction(()=>document.querySelector('[name="camera-0-rtsp-main"]')?.value.includes('/discovered/main'));
 await page.locator('#save-config').click();
 await page.waitForFunction(()=>document.querySelector('#save-state')?.textContent.includes('Home Assistant add-on options updated.'));
 saved=await page.evaluate(()=>fetch('api/config').then(r=>r.json()));
 url=new URL(saved.cameras[0].rtsp_main);assert.equal(url.pathname,'/discovered/main');assert.equal(url.port,'8554');assert.equal(decodeURIComponent(url.password),'test@new:#/?');
 assert.equal(errors.length,0,errors.join('\n'));
 console.log('PASS: vendor change, edited IP and reserved password, saved/reloaded Reolink profile, confirmed options-sync status and ONVIF-discovered custom paths');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
