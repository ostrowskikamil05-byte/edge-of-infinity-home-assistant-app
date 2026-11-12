// Real archive pictures through the unchanged transport; make repeated platform
// capability probes costly to verify their removal without changing the media.
const {chromium,webkit}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18095';
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 try{
  const page=await browser.newPage();page.setDefaultTimeout(20000);const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base);
  await page.evaluate(async()=>{
   const probe=VideoDecoder.isConfigSupported.bind(VideoDecoder);window.probes=[];
   VideoDecoder.isConfigSupported=async config=>{await new Promise(resolve=>setTimeout(resolve,150));const result=await probe(config);probes.push({codec:config.codec,hardware:config.hardwareAcceleration,supported:result.supported});return result;};
   const {EdgeDVRClient}=await import('/api/edge-rtc/edge-dvr-client.js');
   const canvas=document.createElement('canvas');document.body.append(canvas);window.notes=[];window.frames=[];
   window.client=new EdgeDVRClient({url:'/api/edge-rtc/archive/back_1',canvas,diagnostics:{note:(event,data)=>notes.push({event,data}),registerSource(){},unregisterSource(){},source(){}},onFrame:()=>{const data=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;let hash=0;for(let i=0;i<data.length;i+=16)hash=(Math.imul(hash,31)+data[i])>>>0;frames.push({hash,position:client.position,seekMS:client.seekMS});},onError:error=>{throw Error(error);}});
   client.setAudio(.6,true);await client.start();
  });
  await page.waitForFunction(()=>client.socketReady);
  for(let epoch=1;epoch<=4;epoch++){
   // Exercise indexed seeks, not the separate adjacent decoded-frame shortcut.
   await page.evaluate(()=>{client.clearFrames();client.seek('20260728-000000.mp4',1,{paused:true});});
   await page.waitForFunction(epoch=>client.epoch===epoch&&client.picture&&client.position>=1&&client.position<1.1,epoch);
  }
  const result=await page.evaluate(()=>({frames,probes,notes:notes.filter(e=>e.event==='dvr_index_ready'),state:client.diagnosticState(),socket:client.ws.readyState}));
  assert.equal(result.frames.length,4);assert(result.frames.every(f=>f.hash===result.frames[0].hash&&f.hash!==0),'warm seeks changed/blanked the selected picture');
  assert(result.notes.slice(1).every(e=>e.data.capability_cache_hit),'warm seek repeated a positive capability query');
  const positive=result.probes.filter(p=>p.supported);assert.equal(positive.length,1,'same positive profile was queried repeatedly');
  assert.equal(result.socket,1);assert.deepEqual(errors,[]);
  await page.evaluate(()=>client.close());
  console.log(`PASS ${wk?'webkit':'chromium'}: exact picture/time on four same-socket seeks, one positive capability probe; first ${Math.round(result.frames[0].seekMS)} ms, warm ${result.frames.slice(1).map(f=>Math.round(f.seekMS)).join('/')} ms with an injected 150 ms probe delay`);
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
