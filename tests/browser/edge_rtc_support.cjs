const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www');

(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 try{
  for(const [scheme,decoder,code] of [['http',true,'insecure_context'],['https',false,'webcodecs_unavailable'],['https',true,'supported']]){
   const context=await browser.newContext();
   if(!decoder)await context.addInitScript(()=>Object.defineProperty(window,'VideoDecoder',{configurable:true,value:undefined}));
   const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.route('**/*',route=>{
    const name=new URL(route.request().url()).pathname;
    if(name==='/client.js'||name==='/edge/edge-rtc-client.js')return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(root,'edge-rtc-client.js'),'utf8')});
    if(name==='/card.js')return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(root,'edge-live-card.js'),'utf8')});
    return route.fulfill({contentType:'text/html',body:'<!doctype html><canvas></canvas>'});
   });
   await page.goto(`${scheme}://edge-test.invalid/`);
   const result=await page.evaluate(async()=>{
    const {EdgeRTCClient,edgeRTCPlaybackSupport}=await import('/client.js');
    const support=edgeRTCPlaybackSupport('pl');
    if(support.supported)return {support,secure:isSecureContext};
    window.signCalls=0;window.stoppedTracks=0;
    const client=new EdgeRTCClient({canvas:document.querySelector('canvas'),hass:{language:'pl',callWS:async()=>{signCalls++;throw new Error('signing must not be reached');}},microphone:{getTracks:()=>[{stop:()=>stoppedTracks++}]}});
    let failure;
    try{await client.start();}catch(error){failure={code:error.code,retryable:error.retryable,message:error.message};}
    return {support,secure:isSecureContext,failure,closed:client.closed,signCalls,stoppedTracks};
   });
   assert.equal(result.support.code,code);
   if(code!=='supported'){
    assert.equal(result.failure.code,code);assert.equal(result.failure.retryable,false);assert(result.closed);assert.equal(result.signCalls,0);assert.equal(result.stoppedTracks,1);
    assert(result.failure.message.includes(code==='insecure_context'?'HTTPS':'WebCodecs'));
    await page.addScriptTag({url:'/card.js'});
    await page.evaluate(()=>{
     window.signCalls=0;
     window.hass={connection:{},locale:{language:'pl'},states:{'camera.front':{state:'idle',attributes:{edge_stream_source:'edge_rtc',edge_profiles:['nvr'],edge_stream_entities:{nvr:'camera.front'}}}},callWS:async()=>{signCalls++;throw new Error('signing must not be reached');}};
     window.card=document.createElement('edge-live-card');card.setConfig({entity:'camera.front',reconnect:true});card.hass=hass;document.body.append(card);
    });
    await page.waitForFunction(()=>[...card._players.values()].every(player=>player._hardFailed));
    const state=await page.evaluate(()=>{const p=[...card._players.values()][0];return{retryTimer:p._retryTimer,message:p._status.textContent,signCalls};});
    assert.equal(state.retryTimer,null);assert.equal(state.signCalls,0);assert(state.message.includes(code==='insecure_context'?'HTTPS':'WebCodecs'));
   }
   assert.deepEqual(errors,[]);
   console.log(`PASS ${scheme}, decoder=${decoder}: ${code}${code==='supported'?'':'; blocked clients release microphone and do not sign/retry'}`);
   await context.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
