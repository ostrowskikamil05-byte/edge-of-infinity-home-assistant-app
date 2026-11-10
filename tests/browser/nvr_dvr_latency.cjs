// Compare the negotiated legacy/fast protocols through the same real archive
// decoder and original files, with 100 ms delay in each WebSocket direction.
const {chromium,webkit}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),{spawn}=require('node:child_process');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',backend=new URL(process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18095');
 assert.equal(backend.hostname,'127.0.0.1','latency benchmark requires a local disposable fixture');
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'edge-dvr-latency-')),info=path.join(folder,'proxy.json');
 const proxy=spawn(process.env.PYTHON||'python3',[path.join(__dirname,'network_latency_proxy.py'),'--backend-port',backend.port,'--info',info],{stdio:['ignore','ignore','inherit']});
 const browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']});
 const results={};let currentPage,currentMode;
 try{
  const deadline=Date.now()+10000;
  while(!fs.existsSync(info)){assert(proxy.exitCode===null&&Date.now()<deadline,'latency proxy failed');await new Promise(r=>setTimeout(r,20));}
  const base='http://127.0.0.1:'+JSON.parse(fs.readFileSync(info)).port;
  for(const mode of['legacy','fast']){
   const context=await browser.newContext({viewport:{width:430,height:900},isMobile:true,hasTouch:true}),page=currentPage=await context.newPage();currentMode=mode;page.setDefaultTimeout(20000);
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   page.on('websocket',socket=>socket.on('socketerror',error=>console.error('WS transport',error)));
   // Load a real loopback response: synthetic fulfilled documents are public
   // address-space pages in Chromium and cannot open local-network sockets.
   await page.goto(base);
   await page.evaluate(async()=>{const {EdgeDVRClient}=await import('/api/edge-rtc/edge-dvr-client.js');const canvas=document.createElement('canvas');canvas.style='position:fixed;inset:80px auto auto 10px;max-width:90%;z-index:9999';document.body.append(canvas);window.archiveErrors=[];window.bench=new EdgeDVRClient({url:'/api/edge-rtc/archive/back_1',canvas,onError:e=>archiveErrors.push(e)});bench.setAudio(.6,true);await bench.start();});
   await page.waitForFunction(()=>bench.socketReady);
   if(mode==='legacy')await page.evaluate(()=>{bench.archiveBatchPackets=16;bench.archiveInitialBatch=false;bench.archiveWindow=1;});
   await page.evaluate(()=>bench.seek('20260728-000000.mp4',18,{paused:true}));
   await page.waitForFunction(()=>bench.picture&&bench.position>=18&&bench.position<18.2);
   results[mode]=await page.evaluate(()=>bench.diagnosticState());
   assert.deepEqual(await page.evaluate(()=>archiveErrors),[]);
   if(mode==='fast'){
    await page.evaluate(()=>{bench.seek('20260728-000000.mp4',20,{paused:true});bench.seek('20260728-000000.mp4',2,{paused:true});bench.seek('20260728-000000.mp4',12.35,{paused:true});});
    await page.waitForFunction(()=>bench.picture&&bench.position>=12.35&&bench.position<12.5);
    const state=await page.evaluate(()=>bench.diagnosticState());assert(state.archive_pending_pulls<=2&&state.archive_buffer_frames<=24&&state.message_queue>=0&&state.message_bytes<=24*1024*1024);assert.deepEqual(await page.evaluate(()=>archiveErrors),[]);
   }
   await page.evaluate(()=>bench.close());assert.deepEqual(errors,[]);await context.close();
  }
  assert(results.fast.seek_ms<results.legacy.seek_ms*.65&&results.legacy.seek_ms-results.fast.seek_ms>350,'seek did not improve under RTT: '+JSON.stringify(results));
  assert(results.fast.seek_ms<2500,'fast archive seek still waited too long: '+JSON.stringify(results.fast));
  console.log(`PASS ${wk?'webkit':'chromium'}: 200 ms RTT seek legacy=${Math.round(results.legacy.seek_ms)} ms fast=${Math.round(results.fast.seek_ms)} ms; latest seek wins, bounded queues, original decoder/files`);
 }catch(error){try{console.error('Latency state',currentMode,JSON.stringify(await currentPage.evaluate(()=>({state:bench.diagnosticState(),errors:archiveErrors}))));}catch(_){}throw error;}finally{await browser.close();proxy.kill('SIGTERM');await new Promise(resolve=>{if(proxy.exitCode!==null)return resolve();const timer=setTimeout(()=>{proxy.kill('SIGKILL');resolve();},3000);proxy.once('exit',()=>{clearTimeout(timer);resolve();});});fs.rmSync(folder,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
