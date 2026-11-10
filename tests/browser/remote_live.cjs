const {chromium}=require('playwright');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const fixtures=JSON.parse(execFileSync(process.env.PYTHON||'python',[path.join(__dirname,'remote_live_fixture.py')],{encoding:'utf8',maxBuffer:4*1024*1024}));

(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  for(const width of [1440,820,390,320]){
   for(const scenario of [
    {mode:'native',host:'edge-test.ui.nabu.casa',prefix:'/api/hassio_ingress/test-session/',publicUrl:''},
    {mode:'native',host:'edge.example.test',prefix:'/',publicUrl:'http://192.0.2.50:8889'},
    ...(width===390?[{mode:'native',host:'edge-test.ui.nabu.casa',prefix:'/api/hassio_ingress/test-session/',publicUrl:'',checking:true}]:[]),
   ]){
    const {mode,host,prefix,publicUrl,checking}=scenario;
    const context=await browser.newContext({viewport:{width,height:1000}});
    const page=await context.newPage();const errors=[],requests=[],events=[];
    page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
    let config=structuredClone(fixtures[mode].config);
    config.live.mobile_webrtc_public_url=publicUrl;
    config.live.mobile_webrtc_turn_password='fixture-turn-value';
    await page.route('**/*',route=>{
     const request=route.request(),url=new URL(request.url());requests.push(url);
     assert.equal(url.host,host,'browser attempted a separate media host');
     assert(url.pathname.startsWith(prefix),'request escaped ingress prefix');
     const endpoint=url.pathname.slice(prefix.length);
     const json=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
     if(!endpoint)return route.fulfill({contentType:'text/html',body:fixtures[mode].html});
     if(endpoint==='api/version')return json({version:fixtures[mode].version,ui_build:fixtures[mode].ui_build});
     if(endpoint==='api/config'){
      if(request.method()==='POST')config=request.postDataJSON();
      return route.fulfill({contentType:'application/json',headers:{'X-Edge-Options-Sync':'synced'},body:JSON.stringify(config)});
     }
     if(endpoint==='cameras.json')return json({cameras:[{id:'front',key:'front_0',index:0,name:'Front',vendor:'hikvision',status:checking?'checking':'online',record_stream:'main',tile_stream:'sub',live_stream:'sub',video_codec:'h264'}]});
     if(endpoint==='api/presets')return json({presets:[]});
     if(endpoint==='api/recording/status')return json({summary:url.searchParams.get('summary')==='1',cameras:[]});
     if(endpoint==='api/storage/status')return json({pending:false,recordings_bytes:0,recordings_volume:{free_bytes:2*1024**3},cache_volume:{free_bytes:2*1024**3}});
     if(endpoint==='api/debug')throw Error('per-event debug HTTP must not be used');
     if(endpoint==='api/client-diagnostics.js')return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www/edge-device-diagnostics.js'),'utf8')});
     if(endpoint==='api/edge-rtc/client.js'||endpoint==='api/edge-rtc/device-diagnostics.js'){
      assert.equal(mode,'native');assert.equal(url.searchParams.get('v'),fixtures[mode].version,'preload URL is not versioned');
      const asset=endpoint.endsWith('/client.js')?'edge-rtc-client.js':'edge-device-diagnostics.js';
      return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www',asset),'utf8')});
     }
     if(endpoint==='api/client-diagnostics'){events.push(...(request.postDataJSON().events||[]));return json({ok:true});}
     if(endpoint.startsWith('api/edge-rtc/player/'))return route.fulfill({contentType:'text/html',body:'<!doctype html><p>Native player routing fixture</p>'});
     throw new Error('Unexpected fixture request: '+endpoint);
    });
    await page.goto(`https://${host}${prefix}`);
    await page.locator('#camera-grid .camera.video-tile').waitFor();
    const native=mode==='native';
    assert.equal(await page.locator('#camera-grid [data-live-blocked]').count(),native?0:1);
    if(native){
     assert.equal(await page.locator('#camera-grid .notice.warning').count(),0);
     const frame=page.locator('#camera-grid iframe');await frame.waitFor();
     assert((await frame.getAttribute('srcdoc')).includes('Inline player routing fixture'),'native player still waits for a separate iframe document');
     assert.equal(new URL(await frame.getAttribute('src'),page.url()).href,`https://${host}${prefix}api/edge-rtc/player/front_main`);
     await page.locator('#camera-grid .preview').dispatchEvent('click');
     await page.waitForFunction(()=>document.querySelectorAll('#camera-grid iframe').length===0);
     await page.locator('#camera-grid .preview').dispatchEvent('click');
     await frame.waitFor();
    }
    assert(requests.filter(url=>url.pathname.endsWith('/api/recording/status')).every(url=>url.searchParams.get('summary')==='1'),'Live boot requested full archive history');
    assert(!requests.some(url=>url.pathname.endsWith('/api/storage/status')),'Live boot requested full storage accounting');
    if(checking){
     await page.waitForRequest(request=>{const u=new URL(request.url());return u.pathname.endsWith('/api/recording/status')&&u.searchParams.get('catalog')==='1';});
     assert(await page.locator('#camera-grid iframe').count(),'warming camera was not started automatically');
    }
    if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();
    const storageResponse=page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/api/storage/status'));
    await page.locator('nav [data-page-target="edge-settings"]').click();
    await storageResponse;
    await page.locator('#network-settings > summary').click();
    assert.equal(await page.locator('[name="live-mobile-public-url"]').count(),0);
    if(native)assert(await page.locator('[data-native-network-info]').isVisible());
    await page.locator('#advanced-settings > summary').click();
    assert.equal(await page.locator('[name="live-engine"]').count(),0);
    await page.locator('[name="nvr-retention-days"]').fill('21');
    await page.locator('#save-edge-settings').click();
    await page.waitForFunction(()=>document.getElementById('edge-save-state').textContent.startsWith('Saved and verified.'));
    assert.deepEqual(config.live,{engine:'edge_rtc'},'retired transport settings survived saving');
    assert.equal(config.storage.retention_days,21);
    if(native){const toggle=[...events,...await page.evaluate(()=>deviceReporter?.events||[])].filter(e=>e.event==='ui_live_toggle');assert(toggle.length>=2);assert(toggle.every(e=>e.data.reason==='edge_rtc'&&e.data.can_embed));}
    assert(requests.every(url=>url.port===''),'extra media port requested');
    assert.deepEqual(errors,[]);
    console.log(`PASS ${mode} ${host} ${width}px: notice, live routing, ingress, native-only settings and camera source routing`);
    await context.close();
   }
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
