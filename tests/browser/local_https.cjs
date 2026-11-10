// Opt-in setup and the same HA DVR card over the actual Go TLS gateway.
// Browser ignores this disposable CA only here; Go tests verify CA trust and SANs.
const {chromium,webkit}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const info=JSON.parse(fs.readFileSync(process.env.EDGE_NVR_FIXTURE_INFO));assert(info.core_config,'requires a disposable fixture config');
 const panel=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18095',bridge=process.env.EDGE_HA_BRIDGE_URL||'http://127.0.0.1:18195';
 assert.equal(new URL(panel).hostname,'127.0.0.1');assert.equal(new URL(bridge).hostname,'127.0.0.1');
 const original=fs.readFileSync(info.core_config,'utf8'),config=JSON.parse(original);
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
 const headers={Authorization:'Bearer '+info.archive_token};const request=await browser.newContext();let tlsContext;
 try{
  const wait=async fn=>{const until=Date.now()+10000;while(!await fn()){assert(Date.now()<until,'local gateway did not reload');await new Promise(r=>setTimeout(r,100));}};
  const status=async()=>(await request.request.get(info.archive_core+'/api/local-https',{headers})).json();
  config.local_https={enabled:false,listen:'127.0.0.1:0',directory:path.join(path.dirname(info.core_config),'local-https'),upstream:bridge};fs.writeFileSync(info.core_config,JSON.stringify(config));
  await wait(async()=>(await status()).enabled===false);
  const before=await(await request.request.get(panel+'/api/config')).json();
  const page=await request.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));await page.goto(panel);
  await page.evaluate(async()=>{await import('/api/edge-rtc/edge-local-network.js');const element=document.createElement('edge-local-network');element.id='fixture-local-setup';element.endpoint='/api/network/local';document.body.append(element);window.setup=element;window.unrelatedChanges=0;document.addEventListener('change',()=>unrelatedChanges++);await element.load();});
  await page.locator('#fixture-local-setup [data-host]').fill('127.0.0.1');await page.locator('#fixture-local-setup [data-names]').fill('localhost');
  await page.locator('#fixture-local-setup [data-prepare]').click();await page.waitForFunction(()=>setup.data?.gateway?.certificate_ready);
  assert(await page.locator('#fixture-local-setup [data-ca]').isVisible());assert((await page.locator('#fixture-local-setup [data-yaml]').textContent()).includes('trusted_proxies:'));
  assert.equal(await page.evaluate(()=>unrelatedChanges),0,'local settings dirtied unrelated camera settings');
  const ca=await request.request.get(panel+'/api/network/local/ca.crt');assert.equal(ca.status(),200);assert((await ca.text()).includes('BEGIN CERTIFICATE'));assert(!(await ca.text()).includes('PRIVATE KEY'));
  assert.equal((await request.request.post(panel+'/api/network/local',{data:{enabled:true,hosts:['192.0.2.123']}})).status(),400,'enabled a certificate/address mismatch');
  assert.deepEqual(await(await request.request.get(panel+'/api/config')).json(),before,'TLS setup changed cameras or recording options');
  assert.equal((await request.request.get(info.archive_core+'/api/local-https')).status(),401,'exposed private TLS setup API');
  assert.equal((await request.request.post(panel+'/api/network/local',{headers:{'Content-Type':'text/plain'},data:'{}'})).status(),403);
  config.local_https.enabled=true;fs.writeFileSync(info.core_config,JSON.stringify(config));await wait(async()=>(await status()).active===true);
  const state=await status();const base='https://'+state.listen;
  tlsContext=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:430,height:900},isMobile:true,hasTouch:true});const tls=await tlsContext.newPage();const connections=[];tls.on('websocket',socket=>connections.push(socket.url()));
  // HA authentication remains mandatory through TLS; the private core has no route here.
  assert.equal((await tlsContext.request.get(base+'/api/edge/dashboard/status/camera.back')).status(),401);
  await tls.goto(base+'/?cards=recordings&recordings='+encodeURIComponent(JSON.stringify({cameras:['camera.back'],muted:true})));
  assert(await tls.evaluate(()=>isSecureContext&&typeof VideoDecoder==='function'));
  await tls.waitForFunction(()=>document.querySelector('edge-recordings-card')._files.length===3);
  await tls.evaluate(()=>{window.rec=document.querySelector('edge-recordings-card');rec._seek(rec._files[0][1]+2,true);});await tls.waitForFunction(()=>rec._dvr?.picture&&rec._dvr.frames>=6);
  assert(connections.some(url=>url.startsWith('wss://'+state.listen+'/api/edge/dashboard/archive/')),'archive did not use the local TLS HA route');
  await tls.evaluate(()=>rec._seek(rec._files[0][1]+6.2,true));await tls.waitForFunction(()=>rec._dvr.picture&&rec._dvr.position>=6.2);
  const fingerprint=state.certificate_fingerprint;
  assert.equal((await request.request.post(info.archive_core+'/api/local-https/certificate',{headers,data:{hosts:['localhost']}})).status(),409,'replaced an active certificate');
  config.local_https.enabled=false;fs.writeFileSync(info.core_config,JSON.stringify(config));await wait(async()=>(await status()).active===false);
  assert.equal((await status()).certificate_fingerprint,fingerprint);assert.deepEqual(errors,[]);
  console.log('PASS: local HTTPS setup, public CA only, unchanged camera/recording config, HA auth, secure WebCodecs + WSS DVR, opt-in listener lifecycle');
 }finally{fs.writeFileSync(info.core_config,original);await tlsContext?.close();await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
