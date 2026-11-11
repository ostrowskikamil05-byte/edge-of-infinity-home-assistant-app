const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  const page=await browser.newPage(),base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088';
  await page.goto(base);await page.locator('.brand img').evaluate(image=>image.decode());
  for(const name of['icon.png','logo.png','logo-light.png']){
   const alpha=await page.evaluate(async name=>{const image=new Image();image.src=new URL('api/brand/'+name+'?v=0.17.1',document.baseURI);await image.decode();const c=document.createElement('canvas');c.width=image.naturalWidth;c.height=image.naturalHeight;const g=c.getContext('2d');g.drawImage(image,0,0);return {width:c.width,height:c.height,alpha:g.getImageData(0,0,1,1).data[3]};},name);
   assert(alpha.width>100&&alpha.height>100);assert.equal(alpha.alpha,0,'logo background is opaque');
  }
  const first=await page.request.get(base+'/api/brand/icon.png?v=0.17.1');assert.equal(first.status(),200);assert(first.headers()['cache-control'].includes('immutable'));
  const cached=await page.request.get(base+'/api/brand/icon.png?v=0.17.1',{headers:{'If-None-Match':first.headers().etag}});assert.equal(cached.status(),304);
  for(const width of[1440,820,430,320]){await page.setViewportSize({width,height:900});await page.waitForTimeout(60);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'branding overflows '+width);}
  console.log('PASS: transparent logo/mark assets, cache validation and responsive branding at desktop/tablet/phone widths');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
