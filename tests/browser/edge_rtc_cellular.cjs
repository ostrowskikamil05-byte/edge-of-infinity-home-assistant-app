// Real RTSP -> authenticated HA bridge -> WebCodecs, with receiver-side
// delivery bursts. No second camera source, transcoding or alternate URL.
const {chromium,webkit}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit';
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 let timer,page;
 try{
  const context=await browser.newContext({viewport:{width:430,height:900},isMobile:true,hasTouch:true,userAgent:wk?'Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/HomeAssistant':'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/156 Mobile Safari/537.36'});
  page=await context.newPage();page.setDefaultTimeout(30000);const errors=[],queued=[];let burst=false,socketCount=0;
  page.on('pageerror',e=>errors.push(e.message));
  await page.routeWebSocket('**/api/edge/rtc/**',socket=>{
   socketCount++;const server=socket.connectToServer();
   server.onMessage(message=>{if(burst)queued.push(()=>socket.send(message));else socket.send(message);});
  });
  timer=setInterval(()=>{for(const send of queued.splice(0))send();},360);
  await page.goto('http://127.0.0.1:18089');
  await page.evaluate(async()=>{
   const {EdgeRTCClient}=await import('/edge/edge-rtc-client.js');
   document.body.innerHTML='<button id="start">Live</button><canvas></canvas>';
   window.liveErrors=[];window.frameTimes=[];
   document.querySelector('button').onclick=async()=>{
    window.mobileLive=new EdgeRTCClient({canvas:document.querySelector('canvas'),url:'/api/edge/rtc/camera.front',onError:message=>liveErrors.push(message),onFrame:()=>frameTimes.push(performance.now())});
    mobileLive.setAudio(1,false);await mobileLive.start();
   };
  });
  await page.locator('button').click();await page.waitForFunction(()=>mobileLive.frames>=12);
  assert.equal(await page.evaluate(()=>mobileLive.networkBufferMS||0),0,'steady Wi-Fi acquired extra delay');
  burst=true;
  await page.waitForFunction(()=>mobileLive.networkBufferMS>=160);
  await page.waitForTimeout(2000);
  const before=await page.evaluate(()=>{frameTimes=[];return mobileLive.diagnosticState();});
  await page.waitForTimeout(6000);
  const result=await page.evaluate(()=>({state:mobileLive.diagnosticState(),intervals:frameTimes.slice(1).map((t,i)=>t-frameTimes[i]),errors:liveErrors}));
  const sorted=result.intervals.slice().sort((a,b)=>a-b),p95=sorted[Math.floor(sorted.length*.95)];
  assert.equal(result.state.state,'playing',JSON.stringify(result));assert.deepEqual(result.errors,[]);
  assert(result.state.network_buffer_ms<=400&&result.state.network_buffer_ms>=160);
  assert(result.state.frames-before.frames>=90,'cellular delivery lost source FPS: '+JSON.stringify(result.state));
  assert(p95<110&&Math.max(...sorted)<220,'cellular bursts still freeze the picture: '+JSON.stringify({p95,max:Math.max(...sorted),state:result.state}));
  assert(result.state.render_queue<=32&&result.state.render_ahead_ms<=result.state.render_limit_ms+60);
  assert.equal(result.state.audio_underruns,before.audio_underruns,'steady cellular bursts interrupted audio');
  assert.equal(result.state.audio_overruns,before.audio_overruns,'cellular PCM burst exceeded its bound');
  assert.equal(result.state.audio_dropped_packets,0);assert(result.state.audio_output_frames-before.audio_output_frames>48000*4.8);
  assert(Number.isFinite(result.state.network_rtt_ms)&&result.state.network_rtt_ms>=0,'HA bridge blocked read-only timing feedback');
  // An extreme decoder backlog can be discarded only when a complete new
  // keyframe is available. Reuse the same socket and physical camera session.
  burst=false;for(const send of queued.splice(0))send();
  await page.evaluate(()=>{const original=mobileLive.waitForBootstrapCapacity.bind(mobileLive);let release;window.resumeDecoder=()=>release();const gate=new Promise(resolve=>release=resolve);mobileLive.waitForBootstrapCapacity=async timestamp=>{await gate;return original(timestamp);};});
  await page.waitForTimeout(2300);await page.evaluate(()=>resumeDecoder());
  await page.waitForFunction(()=>mobileLive.networkRecoveries>0&&mobileLive.frames>150);
  assert.deepEqual(await page.evaluate(()=>liveErrors),[]);assert.equal(socketCount,1);
  const status=await(await page.request.get('http://127.0.0.1:18089/test/status')).json();
  assert.equal(status.sources[0].upstream_connections,1);assert.equal(status.sources[0].connection_attempts,1);
  await page.evaluate(()=>mobileLive.close());assert.deepEqual(errors,[]);
  console.log(`PASS ${wk?'WebKit iPhone':'Chromium Android'}: 360 ms transport bursts, ${result.state.network_buffer_ms} ms adaptive reserve, ${Math.round(p95)} ms p95 frame spacing, uninterrupted PCM, RTT telemetry and safe IDR recovery, one camera/socket`);
  await context.close();
 }catch(error){try{console.error('Cellular state',JSON.stringify(await page?.evaluate(()=>({state:window.mobileLive?.diagnosticState(),errors:window.liveErrors,secure:isSecureContext,decoder:typeof VideoDecoder}))));}catch(_){}throw error;}finally{clearInterval(timer);await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
