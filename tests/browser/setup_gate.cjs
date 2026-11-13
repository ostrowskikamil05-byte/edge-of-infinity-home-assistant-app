const {chromium,webkit}=require('playwright');
const {execFileSync}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const fixture=JSON.parse(execFileSync(process.env.PYTHON||'python',[path.join(__dirname,'remote_live_fixture.py')],{env:{...process.env,EDGE_TEST_LOCKED_SETUP:'1'},encoding:'utf8',maxBuffer:4*1024*1024})).native;
const www=path.resolve(__dirname,'../../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www');
(async()=>{
 const name=process.env.EDGE_TEST_BROWSER==='webkit'?'webkit':'chromium';
 const browser=await ({chromium,webkit}[name]).launch({headless:true,executablePath:name==='webkit'?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,args:name==='chromium'?['--no-sandbox']:[]});
 try{for(const failInitial of [false,true]){
  const context=await browser.newContext({viewport:{width:390,height:844},locale:'pl-PL'}),page=await context.newPage();
  let needsSetup=true,unavailable=failInitial;const errors=[],operations=[];
  page.on('pageerror',e=>errors.push(e.message));
  const base='https://edge.example.test/api/hassio_ingress/test/';
  const state=()=>({managed:true,id:'f'.repeat(32),needs_setup:needsSetup,csrf:'fixture-csrf'});
  await page.route('**/*',route=>{const font=require('./ui_assets.cjs').fontResponse(route.request().url());if(font)return route.fulfill(font);
   const request=route.request(),url=new URL(request.url()),key=url.pathname.slice('/api/hassio_ingress/test/'.length);
   const json=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
   if(url.href===base)return route.fulfill({contentType:'text/html',body:fixture.html});
   if(key==='api/setup')return unavailable?route.fulfill({status:503,body:'{}'}):json(state());
   if(key==='api/setup/access'){const data=request.postDataJSON();assert.equal(data.confirmed,true);needsSetup=false;return json(state());}
   if(key==='api/version')return json({version:fixture.version,ui_build:fixture.ui_build});
   if(key==='api/client-diagnostics'||key==='api/debug')return json({ok:true});
   if(key.startsWith('api/brand/'))return route.fulfill({contentType:'image/png',body:fs.readFileSync(path.resolve(www,'../../../brand/logo.png'))});
   if(key.endsWith('.js')){let file=key.split('/').pop();if(['client-diagnostics.js','device-diagnostics.js'].includes(file))file='edge-device-diagnostics.js';return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(www,file),'utf8')});}
   assert.equal(needsSetup,false,'operational request escaped mandatory setup: '+key);operations.push(key);
   if(key==='api/config')return json({...fixture.config,cameras:[]});
   if(key==='cameras.json'||key==='api/recording/status')return json({cameras:[]});
   if(key==='api/presets')return json({presets:[]});
   if(key==='api/storage/status')return json({pending:false});
   if(key==='api/events')return json({events:[]});
   throw Error('unexpected fixture request '+key);
  });
  await page.goto(base);
  if(failInitial){await page.waitForFunction(()=>document.querySelector('#edge-setup-check-state').textContent.includes('Nie udało'));
   assert(await page.locator('.app').isHidden());assert.equal(await page.locator('.app').evaluate(e=>e.inert),true);
   assert.deepEqual(operations,[]);unavailable=false;await page.getByRole('button',{name:'Spróbuj ponownie / Retry',exact:true}).click();}
  const dialog=page.locator('edge-first-run dialog');await dialog.waitFor();
  await page.keyboard.press('Escape');assert(await dialog.isVisible());
  assert(await page.locator('.app').isHidden());assert.equal(await page.locator('.app').evaluate(e=>e.inert),true);
  assert.deepEqual(operations,[]);
  await dialog.locator('[data-ack]').check();
  await dialog.getByRole('button',{name:'Zapisz wybór i przejdź dalej',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.app')&&!document.querySelector('.app').hidden&&!document.querySelector('.app').inert);
  await page.locator('#camera-grid [data-add-camera-tile]').waitFor();
  assert(operations.includes('api/config'));assert.deepEqual(errors,[]);
  await context.close();console.log(`PASS ${name}: full panel stays hidden/inert until setup, Escape blocked, status outage fail-closed=${failInitial}, confirmed completion reloads normal app`);
 }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
