const {chromium,webkit}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const www=path.resolve(__dirname,'../../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www');
(async()=>{
 const engine=process.env.EDGE_TEST_BROWSER==='webkit'?'webkit':'chromium';
 const executablePath=engine==='webkit'?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
 const browser=await ({webkit,chromium}[engine]).launch({headless:true,executablePath,args:engine==='chromium'?['--no-sandbox']:[]});
 try{for(const width of [390,320,1024]){
  const context=await browser.newContext({viewport:{width,height:844},locale:'pl-PL'}),page=await context.newPage(),errors=[],posts=[];
  page.on('pageerror',e=>errors.push(e.message));
  let state={managed:true,id:'original',needs_setup:true,legacy_upgrade:true,retained_locations:1,csrf:'fixture-csrf'};
  const base='https://edge.example.test/api/hassio_ingress/fixture/';
  await page.route('**/*',route=>{const url=new URL(route.request().url()),name=url.pathname.split('/').pop();
   const json=d=>route.fulfill({contentType:'application/json',body:JSON.stringify(d)});
   if(url.href===base)return route.fulfill({contentType:'text/html',body:`<html lang="pl"><body><script type="module">import {openSetup} from './edge-setup.js';window.openSetup=()=>openSetup({endpoint:'${base}api/setup',networkEndpoint:'${base}api/network/local',brand:'${base}logo.png'});window.openSetup();</script></body></html>`});
   if(['edge-setup.js','edge-local-network.js'].includes(name))return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(www,name),'utf8')});
   if(name==='logo.png')return route.fulfill({contentType:'image/png',body:fs.readFileSync(path.resolve(www,'../../../brand/logo.png'))});
   if(url.pathname.endsWith('/api/network/local'))return json({enabled:false,hosts:[],gateway:{},urls:[]});
   if(url.pathname.includes('/api/setup')){
    if(route.request().method()==='POST'){
     const data=route.request().postDataJSON();posts.push({name,data});assert.equal(data.csrf,'fixture-csrf');
     if(name==='access')state={...state,needs_setup:false,mode:data.mode};
     if(name==='reset')state={...state,pending_reset:true};
    }return json(state);
   }throw Error('unexpected fixture request '+url.pathname);
  });
  await page.goto(base);
  const dialog=page.locator('edge-first-run dialog');await dialog.waitFor();
  assert(await dialog.locator('[data-legacy]').isVisible());
  assert.equal(await dialog.locator('[data-close]').isVisible(),false);
  assert.equal(await dialog.locator('[data-save]').first().isEnabled(),false);
  await page.keyboard.press('Escape');assert(await dialog.isVisible());
  await dialog.evaluate(node=>node.close());
  await page.waitForFunction(()=>document.querySelector('edge-first-run').shadowRoot.querySelector('dialog').open);
  await dialog.locator('[data-mode="local"]').click();
  const network=dialog.locator('edge-local-network');await network.locator('[data-host]').waitFor();
  assert.equal(await network.locator('[data-host]').inputValue(),'');
  assert.equal(await network.locator('[data-names]').inputValue(),'');
  assert(await dialog.locator('[data-local]').isVisible());
  await dialog.locator('[data-ack]').check();
  await dialog.getByRole('button',{name:'Zapisz wybór i przejdź dalej',exact:true}).click();await dialog.waitFor({state:'detached'});
  assert.equal(posts[0].data.mode,'local');assert.deepEqual(Object.keys(posts[0].data).sort(),['confirmed','csrf','mode']);
  assert.equal(posts[0].data.confirmed,true);
  await page.evaluate(()=>window.openSetup());await dialog.waitFor();
  assert.equal(await dialog.locator('[data-close]').isVisible(),true);
  await dialog.locator('[data-remove] summary').click();
  assert.equal(await dialog.locator('[data-media]').isChecked(),false);
  assert.equal(await dialog.locator('[data-reset]').isEnabled(),false);
  await dialog.locator('[data-confirm]').fill('wrong');assert.equal(await dialog.locator('[data-reset]').isEnabled(),false);
  await dialog.locator('[data-confirm]').fill('DELETE EDGE');await dialog.locator('[data-media]').check();
  await dialog.locator('[data-reset]').click();
  await page.waitForFunction(()=>document.querySelector('edge-first-run')?.shadowRoot.querySelector('output').textContent.includes('Zapisano reset'));
  assert.deepEqual(posts[1].data,{csrf:'fixture-csrf',confirm:'DELETE EDGE',include_media:true});
  const box=await dialog.boundingBox();assert(box.width<=width&&box.x>=0,'setup exceeds phone width');
  assert.deepEqual(errors,[]);await context.close();console.log(`PASS ${engine} setup ${width}px: guides, blank address fields, explicit deletion confirmation, pending restart`);
 }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
