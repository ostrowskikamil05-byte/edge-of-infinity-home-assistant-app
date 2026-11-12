const {chromium,webkit}=require('playwright'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const www=path.resolve(__dirname,'../../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www');
(async()=>{
 const engine=process.env.EDGE_TEST_BROWSER==='webkit'?'webkit':'chromium';
 const browser=await ({chromium,webkit}[engine]).launch({headless:true,executablePath:engine==='webkit'?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,args:engine==='chromium'?['--no-sandbox']:[]});
 try{for(const withEntry of [false,true]){
  const context=await browser.newContext({viewport:{width:390,height:844},locale:'pl-PL'}),page=await context.newPage(),posts=[],errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const base='https://edge.example.test/api/hassio_ingress/fixture/';
  let state={phase:'idle',csrf:'fixture-csrf',registry:{known:true,entries:withEntry?['local-entry']:[],total:withEntry?1:0}};
  await page.route('**/*',route=>{
   const req=route.request(),url=new URL(req.url()),name=url.pathname.split('/').pop();
   const json=d=>route.fulfill({contentType:'application/json',body:JSON.stringify(d)});
   if(url.href===base)return route.fulfill({contentType:'text/html',body:`<html lang="pl"><script type="module">import{openUninstall}from'./edge-uninstall.js';window.wsCalls=[];const hass=${withEntry?"{callWS:async message=>{wsCalls.push(message);return {available:true};}}":"null"};openUninstall({endpoint:'${base}api/uninstall',hass});</script></html>`});
   if(name==='edge-uninstall.js')return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(www,name),'utf8')});
   if(url.pathname.includes('/api/uninstall')){
    if(req.method()==='POST'){
     const data=req.postDataJSON();posts.push({name,data});assert.equal(data.csrf,'fixture-csrf');
     if(name==='plan')state={...state,id:'fixture-job',phase:'planned',scopes:data.scopes,retained_path:'/homeassistant/edge-retained/fixture-job',slug:'fixture_edge'};
     if(name==='commit'){assert.equal(data.confirm,'UNINSTALL EDGE');state={...state,phase:withEntry?'awaiting_integration':'queued'};}
     if(name==='run')state={...state,phase:'queued'};
    }return json(state);
   }throw Error('unexpected fixture request '+url.pathname);
  });
  await page.goto(base);const dialog=page.locator('edge-uninstall dialog');await dialog.waitFor();
  assert.equal(await dialog.locator('[data-scope]').count(),9);
  assert.equal(await dialog.locator('[data-scope]:checked').count(),0);
  await dialog.locator('[data-all]').check();assert.equal(await dialog.locator('[data-scope]:checked').count(),9);
  await dialog.locator('[data-scope=recordings]').uncheck();assert.equal(await dialog.locator('[data-all]').isChecked(),false);
  await dialog.locator('[data-preview]').click();
  await dialog.locator('[data-review]').waitFor();
  assert((await dialog.locator('[data-keep]').textContent()).includes('Nagrania z kamer'));
  assert.equal(posts.length,1,'preview must not queue deletion');
  assert.equal(posts[0].data.scopes.recordings,false);
  assert.equal(await dialog.locator('[data-commit]').isEnabled(),false);
  await dialog.locator('[data-confirm]').fill('wrong');assert.equal(await dialog.locator('[data-commit]').isEnabled(),false);
  await dialog.locator('[data-confirm]').fill('UNINSTALL EDGE');await dialog.locator('[data-commit]').click();
  await page.waitForFunction(()=>document.querySelector('edge-uninstall')?.shadowRoot.querySelector('output').textContent.includes('Deinstalacja zaplanowana'));
  assert.equal(await dialog.locator('[data-cancel]').isEnabled(),false);
  assert.equal(posts.filter(x=>x.name==='commit').length,1);
  assert.equal(posts.filter(x=>x.name==='run').length,withEntry?1:0);
  const calls=await page.evaluate(()=>wsCalls);assert.equal(calls.filter(x=>x.type==='edge/uninstall_integration').length,withEntry?1:0);
  assert.deepEqual(errors,[]);await context.close();console.log(`PASS ${engine}: uninstall selectors/all, review, typed confirmation, preservation and HA integration orchestration=${withEntry}`);
 }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
