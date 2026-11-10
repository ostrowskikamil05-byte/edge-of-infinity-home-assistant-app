// The device can display video but rejects AAC in WebCodecs: retain AAC in
// the file and automatically use its native MP4 decoder, without camera edits.
const {chromium,webkit}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:wk?'Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/HomeAssistant':'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/156 Mobile Safari/537.36'});page.setDefaultTimeout(30000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{localStorage.setItem('edge-archive-transport','edge_rtc');const supported=VideoDecoder.isConfigSupported.bind(VideoDecoder);VideoDecoder.isConfigSupported=c=>/^(hev1|hvc1)\./.test(c.codec)?Promise.resolve({supported:false,config:c}):supported(c);if(typeof AudioDecoder==='function')AudioDecoder.isConfigSupported=c=>Promise.resolve({supported:false,config:c});});
  await page.goto(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18095');await page.locator('[data-camera-form]').first().waitFor({state:'attached'});if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
  await page.waitForFunction(()=>archivePlayers.get('0')?.client?.closed&&archivePlayers.has('1'));
  const host=page.locator('[data-dvr-host="1"]');await host.locator('[data-dvr-action="toggle"]').click();await page.waitForFunction(()=>archivePlayers.get('1')?.client?.picture&&archivePlayers.get('1').wantPlay);
  await host.locator('[data-dvr-action="sound"]').click();
  await page.waitForFunction(()=>{const p=archivePlayers.get('1');return p.client?.picture&&p.wantPlay&&p.client.muted&&p.client.position>.1;});
  assert.equal(await page.locator('video[data-recording-player]').count(),0);assert.equal(await page.locator('[data-recording-download="0"]').count(),1);assert.deepEqual(errors,[]);
  console.log(`PASS ${wk?'webkit':'chromium'}: unsupported video explains the error and offers download; unsupported audio keeps native video playing without editing original files`);

 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
