// Hold signing and the real diagnostics module independently: both must start
// before either finishes. This fixture tests startup orchestration; the separate
// native browser tests verify actual camera/archive pixels, AAC and source count.
const {chromium,webkit}=require('playwright'),assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const root=path.resolve('edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www');
 const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');res.setHeader('Cache-Control','no-store');
  if(url.pathname.startsWith('/edge/')){
   const name=url.pathname.slice(6);if(!/^[a-z0-9-]+\.(js|png)$/.test(name)||!fs.existsSync(path.join(root,name))){res.writeHead(404);res.end();return;}
   res.setHeader('Content-Type',name.endsWith('.js')?'application/javascript':'image/png');res.end(fs.readFileSync(path.join(root,name)));return;
  }
  if(url.pathname.startsWith('/api/')){res.setHeader('Content-Type','application/json');res.end('{"ok":true}');return;}
  res.setHeader('Content-Type','text/html');res.end('<!doctype html><html><body></body></html>');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base='http://127.0.0.1:'+server.address().port;
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{
  for(const surface of ['live','dvr'])for(const action of ['ready','cancel','sign-error','diagnostics-error']){
   const context=await browser.newContext({viewport:{width:430,height:900},hasTouch:true}),page=await context.newPage();page.setDefaultTimeout(10000);const errors=[];let moduleRequested=false,releaseModule;
   const held=new Promise(resolve=>releaseModule=resolve);
   page.on('pageerror',error=>errors.push(error.message));
   await page.route('**/edge/edge-device-diagnostics.js*',async route=>{moduleRequested=true;await held;if(action==='diagnostics-error')await route.fulfill({status:503,body:'fixture diagnostic module unavailable'});else await route.continue();});
   await page.goto(base);
   await page.evaluate(()=>{
    window.fixture={sockets:[],signed:[],signStarted:0};
    window.WebSocket=class{static OPEN=1;constructor(url){this.url=url;this.readyState=1;fixture.sockets.push(this);queueMicrotask(()=>this.onopen?.());}send(){}close(){this.readyState=3;this.onclose?.({code:1000,wasClean:true});}};
    window.hass={connection:{},locale:{language:'pl'},hassUrl:p=>new URL(p,location.href).href,
     states:{'camera.front':{state:'idle',attributes:{edge_camera_id:'front',edge_stream_source:'edge_rtc',edge_profiles:['nvr','main'],edge_stream_entities:{nvr:'camera.front',main:'camera.front'}}}},
     fetchWithAuth:async()=>({ok:true}),callWS:message=>{if(message.type!=='auth/sign_path')throw Error('unexpected signaling call');fixture.signed.push(message);fixture.signStarted=performance.now();return new Promise((resolve,reject)=>{fixture.resolveSign=()=>resolve({path:message.path+(message.path.includes('?')?'&':'?')+'authSig=fixture'});fixture.rejectSign=()=>reject(Error('fixture signing failed'));});}};
   });
   if(surface==='live'){
    await page.addScriptTag({url:base+'/edge/edge-live-card.js'});
    await page.evaluate(()=>{window.card=document.createElement('edge-live-player');card.setConfig({entity:'camera.front',reconnect:false});card.hass=hass;document.body.append(card);});
   }else{
    await page.evaluate(async()=>{const {EdgeDVRClient}=await import('/edge/edge-dvr-client.js');window.client=new EdgeDVRClient({hass,entity:'camera.front',canvas:document.createElement('canvas')});window.startResult=client.start().then(()=>true,error=>{window.startError=error.message;return false;});});
   }
   await page.waitForFunction(()=>fixture.signStarted>0);
   const deadline=Date.now()+5000;while(!moduleRequested){assert(Date.now()<deadline,'diagnostics import waited for signing');await new Promise(r=>setTimeout(r,10));}
   assert.equal(await page.evaluate(()=>fixture.sockets.length),0,'transport opened before signed authorization was ready');
   if(action==='cancel')await page.evaluate(surface=>surface==='live'?card.remove():client.close(),surface);
   await page.evaluate(action=>action==='sign-error'?fixture.rejectSign():fixture.resolveSign(),action);releaseModule();
   if(action==='ready'||action==='diagnostics-error'){
    await page.waitForFunction(()=>fixture.sockets.length===1);
    assert.equal(await page.evaluate(()=>fixture.signed.length),1,'startup signed the same transport twice');
    assert.equal(await page.evaluate(()=>fixture.sockets[0].url.includes('authSig=fixture')),true);
    if(action==='ready'){
     const note=await page.evaluate(surface=>__edgeDeviceReporter.events.find(e=>e.event===(surface==='live'?'rtc_startup_prepared':'dvr_startup_prepared')),surface);
     assert.equal(note?.data.parallel,true,'startup preparation timings were not logged');
     if(surface==='live')assert(await page.evaluate(()=>card._edgeRTC.started<=fixture.signStarted),'first-frame timing excluded preparation');
    }
   }else{
    if(surface==='dvr')await page.evaluate(()=>startResult);
    else{await page.waitForFunction(()=>!card._starting);await page.waitForTimeout(100);}
    assert.equal(await page.evaluate(()=>fixture.sockets.length),0,'cancelled or rejected startup opened a transport');
   }
   await page.evaluate(surface=>surface==='live'?card.remove():client.close(),surface);
   assert(await page.evaluate(()=>fixture.sockets.every(s=>s.readyState===3)),'transport leaked after removal');
   assert.deepEqual(errors,[]);await context.close();
  }
  console.log(`PASS ${wk?'webkit':'chromium'}: Live/DVR parallel signing and diagnostics, exact signed transport, cancellation, authorization failure, diagnostic failure isolation and full startup timing`);
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exit(1);});
