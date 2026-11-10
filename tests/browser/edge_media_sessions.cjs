// Actual camera -> core -> HA Live + DVR cards, with one available video decoder.
// Exercises Apple-style decoder contention without faking decoded pictures.
const {chromium,webkit}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit';
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});let page;
 try{
  const context=await browser.newContext({viewport:{width:430,height:2400},isMobile:true,hasTouch:true,userAgent:wk?'Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/HomeAssistant':'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/156 Mobile Safari/537.36'});page=await context.newPage();page.setDefaultTimeout(20000);
  const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
  await page.goto('http://127.0.0.1:18089');
  await page.evaluate(()=>{
   const Native=VideoDecoder;window.decoders=new Set();window.decoderCollisions=0;window.decoderTrace=[];
   window.VideoDecoder=class extends Native{
    configure(config){decoderTrace.push({event:'configure',active:decoders.size,owner:globalThis[Symbol.for('edge.media.coordinator')]?.owner?.diagnosticKey,stack:Error().stack});if(!decoders.has(this)&&decoders.size>=1){decoderCollisions++;throw Error('Only one hardware decoder is available');}super.configure(config);decoders.add(this);}
    close(){decoderTrace.push({event:'close',active:decoders.size});decoders.delete(this);super.close();}reset(){decoders.delete(this);super.reset();}
   };
   window.hass={connection:{},user:{id:'combined-card-user'},locale:{language:'pl'},hassUrl:p=>new URL(p,location.href).href,
    states:{'camera.front':{state:'idle',attributes:{friendly_name:'Front',edge_camera_id:'front',edge_stream_role:'nvr',edge_stream_source:'edge_rtc',edge_profiles:['nvr'],edge_stream_entities:{nvr:'camera.front'}}}},
    callWS:async msg=>({path:msg.path}),fetchWithAuth:(path,init)=>fetch(path,init),
    callApi:async(method,path)=>{const response=await fetch('/api/'+path);if(!response.ok)throw Error('HA API '+response.status);return response.json();}};
  });
  await page.addScriptTag({url:'/edge/edge-live-card.js'});await page.addScriptTag({url:'/edge/edge-dashboard-cards.js'});
  await page.evaluate(()=>{window.live=document.createElement('edge-live-card');live.setConfig({entity:'camera.front',muted:true});live.hass=hass;document.body.append(live);});
  await page.waitForFunction(()=>[...live._players.values()][0]._edgeRTC?.frames>=5);
  await page.evaluate(()=>{window.recordings=document.createElement('edge-recordings-card');recordings.setConfig({cameras:['camera.front'],muted:true});recordings.hass=hass;document.body.append(recordings);});
  await page.waitForFunction(()=>recordings._files.length===1&&recordings._inView);
  await page.evaluate(()=>recordings._seek(recordings._files[0][1]+2,true));
  await page.waitForFunction(()=>recordings._dvr?.frames>=8&&recordings._dvr.playing);
  assert(await page.evaluate(()=>[...live._players.values()].every(p=>p._mediaSuspended&&!p._edgeRTC)),'Live retained a competing decoder');
  const initial=await page.evaluate(()=>({socket:recordings._dvr.ws.url,width:recordings._dvr.width,height:recordings._dvr.height,notes:recordings._$('.note').textContent}));
  assert.equal(new URL(initial.socket).pathname,'/api/edge/dashboard/archive/camera.front');assert.equal(initial.width,640);assert.equal(initial.height,360);
  for(const seconds of[8.2,1.4,6.1]){
   await page.evaluate(seconds=>recordings._seek(recordings._files[0][1]+seconds,true),seconds);
   await page.waitForFunction(seconds=>recordings._dvr?.picture&&recordings._dvr.position>=seconds&&recordings._dvr.position<seconds+.8,seconds);
   assert.equal(await page.evaluate(()=>recordings._dvr.ws.url),initial.socket);
  }
  assert(await page.evaluate(()=>{const canvas=recordings._canvas,pixels=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;return pixels.some((v,i)=>i%4!==3&&v>60);}),'DVR advanced a clock without actual image pixels');
  // A second DVR viewer immediately yields the first decoder and retains its canvas.
  await page.evaluate(()=>{window.second=document.createElement('edge-recordings-card');second.setConfig({cameras:['camera.front'],muted:true});second.hass=hass;document.body.append(second);});
  await page.waitForFunction(()=>second._files.length===1&&second._inView);
  await page.evaluate(()=>second._seek(second._files[0][1]+3,true));
  await page.waitForFunction(()=>second._dvr?.frames>=4);
  assert(await page.evaluate(()=>recordings._dvr.paused&&!recordings._wantPlay&&recordings._dvr.video===null),'second archive retained first hardware decoder');
  await page.evaluate(()=>{second._wantPlay=false;second._dvr.pause();second._paint();});
  await page.waitForFunction(()=>[...live._players.values()][0]._edgeRTC?.frames>=3);
  assert.equal(await page.evaluate(()=>decoderCollisions),0,'cards exhausted the decoder');
  const status=await page.evaluate(()=>fetch('/test/status').then(r=>r.json()));assert.equal(status.sources[0].upstream_connections,1);assert.equal(status.sources[0].connection_attempts,1);
  assert(!requests.some(url=>/\/recordings\/.*\.mp4/.test(url)),'MP4 playback was reintroduced');assert(!requests.some(url=>url.includes('nabu.casa')),'local playback required the cloud');assert.deepEqual(errors,[]);
  await page.evaluate(()=>{second.remove();recordings.remove();live.remove();});await page.waitForFunction(()=>decoders.size===0);
  console.log(`PASS ${wk?'webkit H265':'chromium H264'}: actual Live + two DVR cards, one GPU decoder, seek pixels, same authenticated socket, one camera upstream, local without cloud`);
 }catch(error){if(page)try{console.error(await page.evaluate(()=>({body:document.body.innerText,decoders:window.decoders?.size,collisions:window.decoderCollisions,trace:window.decoderTrace,coordinator:{live:globalThis[Symbol.for('edge.media.coordinator')]?.live?.size,owner:globalThis[Symbol.for('edge.media.coordinator')]?.owner?.diagnosticKey},players:window.live?[...live._players.values()].map(p=>({suspended:p._mediaSuspended,closed:p._edgeRTC?.closed})):[],recordings:window.recordings?{files:recordings._files.length,note:recordings._$('.note').textContent,state:recordings._dvr?.diagnosticState()}:null})));}catch(_){}throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
