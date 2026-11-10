// Real MP4 sample tables, WebSocket tunnel, WebCodecs pictures and AAC PCM.
const {chromium,webkit}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),crypto=require('node:crypto');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18095',index=process.env.EDGE_TEST_DVR_CAMERA||'1',key=index==='0'?'front_0':'back_1',expectedWidth=Number(process.env.EDGE_TEST_DVR_WIDTH||640),expectedHeight=Number(process.env.EDGE_TEST_DVR_HEIGHT||360),info=JSON.parse(fs.readFileSync(process.env.EDGE_NVR_FIXTURE_INFO||'/workspace/work/dvr-fixture.json'));
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 const originals=new Map(fs.readdirSync(info.recordings_dir).filter(n=>n.endsWith('.mp4')).map(n=>[n,crypto.createHash('sha256').update(fs.readFileSync(info.recordings_dir+'/'+n)).digest('hex')]));
 let page;
 try{
  const context=await browser.newContext({viewport:{width:430,height:900},isMobile:true,hasTouch:true,userAgent:wk?'Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/HomeAssistant':'Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/156 Mobile Safari/537.36'});
  page=await context.newPage();page.setDefaultTimeout(30000);const errors=[],media=[],sockets=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/recordings/'))media.push(r.url());});page.on('websocket',ws=>{if(ws.url().includes('/api/edge-rtc/archive/'))sockets.push(ws.url());});
  await page.addInitScript(index=>{window.fixtureDVRIndex=index;localStorage.setItem('edge-archive-transport','edge_rtc');if(index==='1'&&typeof VideoDecoder==='function'){const supported=VideoDecoder.isConfigSupported.bind(VideoDecoder);VideoDecoder.isConfigSupported=config=>/^(hev1|hvc1)\./.test(config.codec)?Promise.resolve({supported:false,config}):supported(config);}},index);
  await page.goto(base);await page.locator('[data-camera-form]').first().waitFor({state:'attached'});
  if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();await page.locator('nav [data-page-target="nvr"]').click();
  await page.waitForFunction(()=>archivePlayers.has(window.fixtureDVRIndex));
  if(index==='1')await page.waitForFunction(()=>archivePlayers.get('0')?.client?.closed&&archivePlayers.get('0').note.textContent.includes('profil'));
  assert.equal(await page.locator(`[data-nvr-index="${index}"] [data-recording-playback-seek]`).count(),0,'two seek controls were rendered');
  const host=page.locator(`[data-dvr-host="${index}"]`),slider=host.locator('[data-dvr-seek]');
  await host.locator('[data-dvr-action="toggle"]').click();
  await page.waitForFunction(()=>{const p=archivePlayers.get(window.fixtureDVRIndex);return p.client?.picture&&p.wantPlay&&p.position>.2;});
  await page.evaluate(()=>{const p=archivePlayers.get(window.fixtureDVRIndex);window.languageArchive={client:p.client,ws:p.client.ws,epoch:p.client.epoch,position:p.position};EdgeI18n.setPreference('de');});
  await page.waitForTimeout(200);
  assert(await page.evaluate(()=>{const p=archivePlayers.get(window.fixtureDVRIndex);return p.client===languageArchive.client&&p.client.ws===languageArchive.ws&&p.client.epoch===languageArchive.epoch&&p.wantPlay&&p.position>languageArchive.position;}),'language interrupted the archive player');
  await page.evaluate(()=>EdgeI18n.setPreference('auto'));
  const picture=async()=>host.locator('canvas').evaluate(c=>{const small=document.createElement('canvas');small.width=160;small.height=90;const g=small.getContext('2d');g.drawImage(c,0,0,160,90);return {image:small.toDataURL(),bright:[...g.getImageData(0,0,160,90).data].filter((v,i)=>i%4!==3&&v>50).length,width:c.width,height:c.height};});
  const first=await picture();if(process.env.EDGE_DVR_SCREENSHOT)await host.screenshot({path:process.env.EDGE_DVR_SCREENSHOT});assert(first.bright>2000&&first.width===expectedWidth&&first.height===expectedHeight,'actual DVR picture is black or resized');
  const seek=async value=>slider.evaluate((s,value)=>{s.value=String(value);s.dispatchEvent(new Event('input',{bubbles:true}));s.dispatchEvent(new Event('change',{bubbles:true}));},value);
  await seek(18);await seek(3);await seek(6);
  await page.waitForFunction(()=>{const p=archivePlayers.get(window.fixtureDVRIndex);return p.client?.picture&&p.position>=6&&p.position<8;});assert.equal(await host.getAttribute('data-dvr-index-hit'),'true');
  await host.locator('[data-dvr-action="toggle"]').click();assert(await page.evaluate(()=>archivePlayers.get(window.fixtureDVRIndex).client.paused));
  const before=await page.evaluate(()=>archivePlayers.get(window.fixtureDVRIndex).position),beforePicture=await picture();await host.locator('[data-dvr-action="next"]').click();
  await page.waitForFunction(before=>{const p=archivePlayers.get(window.fixtureDVRIndex);return p.client?.picture&&p.client.paused&&p.position>before+.02&&p.position<before+.09;},before);assert.notEqual((await picture()).image,beforePicture.image,'frame step did not change decoded pixels');
  await seek(25);await page.waitForFunction(()=>{const p=archivePlayers.get(window.fixtureDVRIndex);return p.client?.picture&&p.position>=25&&p.position<25.2&&p.client.request.file.endsWith('000024.mp4');});
  assert.equal(sockets.filter(url=>url.endsWith('/'+key)).length,1,'file transition reopened the archive socket');assert(!media.some(url=>url.includes('/recordings/'+key+'/')),'EdgeRTC DVR fetched MP4 bodies');
  await page.waitForTimeout(250);const bytes=await page.evaluate(()=>archivePlayers.get(window.fixtureDVRIndex).client.receivedBytes);await page.waitForTimeout(300);assert.equal(await page.evaluate(()=>archivePlayers.get(window.fixtureDVRIndex).client.receivedBytes),bytes,'paused DVR continued downloading video');
  // A normal Play restart requests references again, then uses the same AAC
  // processor as Live; inspect actual PCM rather than an advancing audio clock.
  await host.locator('[data-dvr-action="sound"]').click();await host.locator('[data-dvr-action="toggle"]').click();
  const audioDecoder=await page.evaluate(()=>typeof AudioDecoder==='function');
  if(audioDecoder){
   await page.evaluate(()=>{const c=archivePlayers.get(window.fixtureDVRIndex).client;c.ensureAudio();window.dvrAnalyser=c.audio.createAnalyser();c.gain.disconnect();c.gain.connect(dvrAnalyser);dvrAnalyser.connect(c.audio.destination);});
   await page.waitForFunction(()=>{const a=new Float32Array(dvrAnalyser.fftSize);dvrAnalyser.getFloatTimeDomainData(a);return a.some(v=>Math.abs(v)>.01);});
   await page.waitForTimeout(500);
   // WebKit can throttle animation callbacks even while audio keeps running.
   // A visible DVR must retain the camera cadence rather than starve AAC.
   if(wk&&expectedWidth<=1920)await page.evaluate(()=>{window.fixtureRAF={request:requestAnimationFrame,cancel:cancelAnimationFrame};window.requestAnimationFrame=callback=>setTimeout(()=>callback(performance.now()),100);window.cancelAnimationFrame=clearTimeout;});
   const continuity=await page.evaluate(()=>new Promise(resolve=>{
    const start=performance.now();let lastSound=start,maxSilence=0;
    const timer=setInterval(()=>{const now=performance.now(),samples=new Float32Array(dvrAnalyser.fftSize);dvrAnalyser.getFloatTimeDomainData(samples);if(samples.some(v=>Math.abs(v)>.01))lastSound=now;maxSilence=Math.max(maxSilence,now-lastSound);if(now-start>=6000){clearInterval(timer);resolve({maxSilence,state:archivePlayers.get(window.fixtureDVRIndex).client.diagnosticState()});}},20);
   }));
   console.log('DVR AAC continuity',JSON.stringify(continuity));
   if(wk&&expectedWidth<=1920){await page.evaluate(()=>{window.requestAnimationFrame=fixtureRAF.request;window.cancelAnimationFrame=fixtureRAF.cancel;});assert(continuity.state.archive_presentation_fallbacks>0,'slow WebKit RAF did not use the media deadline');assert(continuity.state.frames>120,'visible DVR lost its 20 FPS source cadence');}
   assert.equal(continuity.state.state,'playing','DVR stopped during sustained playback');
   assert(continuity.state.audio_output_frames>48000,'DVR did not deliver sustained PCM output');
   assert(continuity.maxSilence<200,'DVR AAC had a sustained silent interruption: '+JSON.stringify(continuity));
   assert.equal(continuity.state.audio_overruns,0,'DVR discarded ahead-of-video PCM');
   assert.equal(continuity.state.audio_dropped_packets,0,'DVR discarded encoded/decoded AAC packets');

  }
  assert((await picture()).bright>2000);
  if(audioDecoder&&expectedWidth<=1920){
   await page.evaluate(()=>archivePlayers.get(window.fixtureDVRIndex).client.setRate(4));
   await page.waitForFunction(()=>{const c=archivePlayers.get(window.fixtureDVRIndex).client;return c.picture&&c.speed===4&&c.playing;});
   const rateStart=await page.evaluate(()=>archivePlayers.get(window.fixtureDVRIndex).position);await page.waitForTimeout(1800);
   const fast=await page.evaluate(()=>{const c=archivePlayers.get(window.fixtureDVRIndex).client,a=new Float32Array(dvrAnalyser.fftSize);dvrAnalyser.getFloatTimeDomainData(a);return{position:archivePlayers.get(window.fixtureDVRIndex).position,state:c.diagnosticState(),audible:a.some(v=>Math.abs(v)>.01)};});
   assert(fast.position-rateStart>5.5&&fast.position-rateStart<8.2,'4x playback did not advance at requested speed: '+JSON.stringify(fast));
   assert(fast.audible,'4x AAC produced no PCM');assert.equal(fast.state.audio_overruns,0);assert.equal(fast.state.audio_dropped_packets,0);
   await page.evaluate(()=>archivePlayers.get(window.fixtureDVRIndex).client.setRate(1));
  }
  await seek(47.8);await page.waitForFunction(()=>{const p=archivePlayers.get(window.fixtureDVRIndex);return p.client?.picture&&p.wantPlay&&p.client.request.file.endsWith('000048.mp4')&&p.position>=48.1;});
  const diagnostics=await page.evaluate(()=>archivePlayers.get(window.fixtureDVRIndex).client.diagnosticState());assert.equal(diagnostics.transport,'edgertc/archive/1');assert(Number.isFinite(diagnostics.seek_ms));assert(diagnostics.archive_buffer_frames<=24);
  await page.evaluate(()=>showPage('home'));assert(await page.evaluate(()=>[...archivePlayers.values()].every(p=>!p.client)),'hidden NVR retained a decoder/socket');
  await page.evaluate(()=>showPage('nvr'));assert.equal(await page.locator('[data-archive-transport]').count(),0);assert.equal(await page.locator('video[data-recording-player]').count(),0);
  const download=await page.locator(`[data-recording-download="${index}"]`).getAttribute('href');assert(download.includes('download=1'));const file=await context.request.get(new URL(download,base).href,{headers:{Range:'bytes=0-1023'}});assert.equal(file.status(),206);assert.equal((await file.body()).length,1024);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'phone UI overflow');assert.deepEqual(errors,[]);for(const [n,hash]of originals)assert.equal(crypto.createHash('sha256').update(fs.readFileSync(info.recordings_dir+'/'+n)).digest('hex'),hash,'archive modified an original recording');
  const status=await context.request.get(info.archive_core+'/api/status',{headers:{Authorization:'Bearer '+info.archive_token}});assert.deepEqual((await status.json()).sources,[],'archive connected to a camera');
  console.log(`PASS ${wk?'webkit':'chromium'}: actual indexed DVR pixels/AAC=${audioDecoder}, latest long-GOP seek ${Math.round(diagnostics.seek_ms)} ms, frame stepping, same-socket file transition, pause stops downloads, original files and MP4 downloads retained`);
  await page.unrouteAll({behavior:'ignoreErrors'});await context.close();
 }catch(error){try{console.error('DVR state',JSON.stringify(await page.evaluate(()=>({players:[...archivePlayers].map(([i,p])=>({i,position:p.position,wantPlay:p.wantPlay,client:p.client?.diagnosticState(),message:p.note.textContent})),page:activePage}))));}catch(_){}throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
