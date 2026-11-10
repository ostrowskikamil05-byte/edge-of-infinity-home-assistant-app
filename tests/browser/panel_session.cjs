const {chromium,webkit}=require('playwright'),{execFileSync}=require('node:child_process'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const fixtures=JSON.parse(execFileSync(process.env.PYTHON||'python',[path.join(__dirname,'remote_live_fixture.py')],{encoding:'utf8',maxBuffer:4*1024*1024}));
async function until(page,predicate){const deadline=Date.now()+15000;while(!predicate()){assert(Date.now()<deadline,'fixture condition timed out');await page.waitForTimeout(50);}}
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}}),f=fixtures.native,host='edge-test.ui.nabu.casa',prefix='/api/hassio_ingress/fixture-session/',requests=[],reports=[],errors=[];
  page.setDefaultTimeout(20000);page.on('pageerror',e=>errors.push(e.message));let held=false,releaseProbe,expired=false;
  await page.route('**/*',async route=>{
   const request=route.request(),u=new URL(request.url());requests.push({path:u.pathname,method:request.method(),at:Date.now()});
   assert.equal(u.host,host);assert(u.pathname.startsWith(prefix));const endpoint=u.pathname.slice(prefix.length).replace(/^\/+/,''),json=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
   if(!endpoint)return route.fulfill({contentType:'text/html',body:f.html});
   assert(!u.pathname.includes('//'),'double slash survived inside ingress URL: '+u.pathname);
   if(endpoint==='api/brand/icon.png')return route.fulfill({contentType:'image/png',body:fs.readFileSync(path.join(__dirname,'../../edge-of-infinity/icon.png'))});
   if(['api/edge-rtc/edge-dvr-client.js','api/edge-rtc/edge-dvr-panel.js'].includes(endpoint))return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www',endpoint.split('/').pop()),'utf8')});
   if(endpoint==='api/version'){
    if(held){await new Promise(resolve=>{releaseProbe=async()=>{await json({version:f.version,ui_build:f.ui_build});resolve();};});return;}
    if(expired)return route.fulfill({status:401,contentType:'text/plain',body:'No valid ingress session'});
    return json({version:f.version,ui_build:f.ui_build});
   }
   if(endpoint==='api/config')return json(f.config);
   if(endpoint==='cameras.json')return json({cameras:[{id:'front',key:'front_0',index:0,status:'online',video_codec:'h264',record_stream:'main'}]});
   if(endpoint==='api/presets')return json({presets:[]});
   if(endpoint==='api/recording/status')return json({summary:true,cameras:[]});
   if(endpoint==='api/core/engine')return json({available:true,core_engine:'edge_rtc',running_engine:'edge_rtc'});
   if(endpoint==='api/edge-rtc/player/front_main')return route.fulfill({contentType:'text/html',body:'<body data-fixture="live">Isolated camera</body>'});
   if(endpoint==='api/client-diagnostics.js'||endpoint==='api/edge-rtc/device-diagnostics.js')return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www/edge-device-diagnostics.js'),'utf8')});
   if(endpoint==='api/edge-rtc/client.js')return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www/edge-rtc-client.js'),'utf8')});
   if(endpoint==='api/client-diagnostics'){const data=request.postDataJSON();assert(Buffer.byteLength(request.postData())<=24000);assert(data.events.length<=32);reports.push({at:Date.now(),data});return json({ok:true});}
   throw Error('Unexpected request: '+endpoint);
  });
  await page.goto(`https://${host}${prefix}/`);await page.locator('#camera-grid iframe').waitFor();await page.waitForFunction(()=>deviceReporter?.device_id);
  await until(page,()=>reports.length>0);const initial=reports.length,firstAt=reports.at(-1).at;
  await page.evaluate(()=>{for(let i=0;i<100;i++)debugEvent('test_burst_'+i,{index:i});return deviceReporter.flush();});await page.waitForTimeout(100);
  assert.equal(reports.length,initial,'events bypassed the telemetry send limit');
  await until(page,()=>reports.length>initial);assert(reports.at(-1).at-firstAt>=4500);assert(reports.at(-1).data.events.some(e=>e.event.startsWith('test_burst_')));
  const visibility=hidden=>page.evaluate(hidden=>{Object.defineProperty(document,'hidden',{configurable:true,value:hidden});document.dispatchEvent(new Event('visibilitychange'));},hidden);
  await visibility(true);const beforeHidden=reports.length;await page.evaluate(()=>{debugEvent('test_hidden');return deviceReporter.flush();});await page.waitForTimeout(100);assert.equal(reports.length,beforeHidden);
  assert.equal(await page.locator('#camera-grid iframe').getAttribute('src'),'about:blank');
  held=true;await visibility(false);await until(page,()=>Boolean(releaseProbe));assert.equal(await page.locator('#camera-grid iframe').getAttribute('src'),'about:blank','stream resumed before session validation');
  held=false;await releaseProbe();await page.waitForFunction(()=>document.querySelector('#camera-grid iframe').dataset.liveSuspended!=='true');
  await page.waitForFunction(()=>{const body=document.querySelector('#camera-grid iframe')?.contentDocument?.body;return body?.textContent.includes('Inline player routing fixture')||body?.dataset.fixture==='live';});
  await visibility(true);expired=true;await visibility(false);await page.locator('[data-ingress-expired]').waitFor();
  assert.equal(await page.locator('#camera-grid iframe').getAttribute('src'),'about:blank');const afterExpired=requests.length;
  await page.evaluate(async()=>{debugEvent('test_expired');await Promise.allSettled([loadCameras(),loadRecordingStatus(),deviceReporter.flush()]);});await page.waitForTimeout(300);
  assert.equal(requests.length,afterExpired,'expired panel kept issuing requests');assert(!requests.some(r=>r.path.includes('/api/debug')));assert.deepEqual(errors,[]);
  console.log(`PASS ${wk?'webkit':'chromium'}: normalized ingress prefix, bounded batched telemetry, hidden-page silence, session check before reconnect and expired-session stop`);
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
