const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const http=require('node:http');
const {chromium,webkit}=require('playwright');
const assets=path.resolve('edge-of-infinity/rootfs/usr/share/edge-of-infinity');
const engine=process.env.EDGE_TEST_BROWSER||'chromium';
let configured=true,authenticated=false,remember=false,held=null,polls=0,requests=[];
const status=()=>({configured,authenticated,csrf:'fixture-csrf',issuer:configured?'https://accounts.example.test':'',client_id:'public-edge',account_url:'https://accounts.example.test/ui/console/users/me',support_email:'support@example.test',profile:authenticated?{email:'member@example.test',name:'<img src=x onerror=alert(1)>'}:null});
const server=http.createServer(async(req,res)=>{
 const url=new URL(req.url,'http://fixture');
 if(url.pathname==='/client.js'){res.setHeader('Content-Type','text/javascript');res.end(fs.readFileSync(path.join(assets,'custom_components/edge/www/edge-accounts.js')));return;}
 if(url.pathname==='/api/brand/logo.png'){res.setHeader('Content-Type','image/png');res.end(fs.readFileSync(path.join(assets,'brand/logo.png')));return;}
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end('<!doctype html><html lang="pl"><meta name="viewport" content="width=device-width"><body style="background:#0e1720;color:white;font:16px sans-serif"><section id="edge-account-settings"></section><button data-settings-jump="network-settings" onclick="this.dataset.opened=true">Network</button><script type="module">import{mountEdgeAccount}from"/client.js";mountEdgeAccount(document.getElementById("edge-account-settings"));</script></body></html>');return;}
 const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=chunks.length?JSON.parse(Buffer.concat(chunks)):null;
 requests.push({path:url.pathname,body,csrf:req.headers['x-edge-csrf']});res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');
 if(req.method==='POST')assert.equal(req.headers['x-edge-csrf'],'fixture-csrf');
 if(url.pathname==='/api/account/status'){res.end(JSON.stringify(status()));return;}
 if(url.pathname==='/api/account/refresh'){res.statusCode=401;res.end('{"error":"account_sign_in_required"}');return;}
 if(url.pathname==='/api/account/provider'){assert.equal(body.issuer,'https://accounts.example.test');configured=true;res.end('{"configured":true}');return;}
 if(url.pathname==='/api/account/device'){remember=body.remember;polls=0;const send=()=>res.end(JSON.stringify({user_code:'ABCD-EFGH',verification_uri_complete:'https://accounts.example.test/activate?code=ABCD-EFGH',interval:5,expires_in:600}));if(held)held.send=send;else send();return;}
 if(url.pathname==='/api/account/poll'){if(++polls<2)res.end('{"pending":true,"interval":5}');else{authenticated=true;res.end(JSON.stringify({pending:false,remember,...status()}));}return;}
 if(url.pathname==='/api/account/logout'){authenticated=false;res.end('{"authenticated":false}');return;}
 res.statusCode=404;res.end('{}');
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const url='http://127.0.0.1:'+server.address().port;
 const browser=await (engine==='webkit'?webkit:chromium).launch({headless:true,...(engine==='chromium'&&process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),...(engine==='webkit'&&process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{const original=window.setTimeout;window.setTimeout=(fn,delay,...args)=>original(fn,delay===5000?30:delay,...args);});
  await page.goto(url);await page.locator('#open').click();await page.locator('#remember').check();await page.locator('#signin').click();
  await page.waitForFunction(()=>document.querySelector('#edge-account-settings').shadowRoot.querySelector('#authorize'));
  assert.equal(await page.locator('#authorize').getAttribute('href'),'https://accounts.example.test/activate?code=ABCD-EFGH');
  assert((await page.locator('#authorize').getAttribute('rel')).includes('noopener'));
  await page.waitForFunction(()=>document.querySelector('#edge-account-settings').shadowRoot.querySelector('#local'));
  assert.equal(remember,true);assert.equal(await page.locator('#edge-account-settings').evaluate(e=>e.shadowRoot.textContent.includes('NVR')),false);
  if(process.env.EDGE_ACCOUNT_SCREENSHOT)await page.screenshot({path:process.env.EDGE_ACCOUNT_SCREENSHOT});
  await page.locator('#local').click();assert.equal(await page.evaluate(()=>localStorage.getItem('edge-access-mode')),'local');
  await page.locator('#guide button').click();assert.equal(await page.locator('[data-settings-jump]').getAttribute('data-opened'),'true');
  await page.locator('#open').click();await page.locator('#identity').waitFor();assert.equal(await page.locator('#identity').textContent(),'member@example.test');assert.equal(await page.locator('a[href="https://accounts.example.test/ui/console/users/me"]').getAttribute('target'),'_blank');
  assert.equal(await page.locator('#edge-account-settings').evaluate(e=>e.shadowRoot.querySelectorAll('img[src="x"]').length),0);
  await page.locator('#logout').click();await page.locator('#signin').waitFor();
  await page.locator('#close').click();configured=false;await page.locator('#open').click();await page.waitForFunction(()=>document.querySelector('#edge-account-settings').shadowRoot.querySelector('#signin')?.disabled);
  await page.locator('summary').click();await page.locator('[name=issuer]').fill('https://accounts.example.test');await page.locator('[name=client_id]').fill('public-edge');await page.locator('[name=support_email]').fill('support@example.test');await page.locator('#provider button').click();await page.waitForFunction(()=>!document.querySelector('#edge-account-settings').shadowRoot.querySelector('#signin')?.disabled);
  held={};await page.locator('#signin').click();while(!held.send)await new Promise(r=>setTimeout(r,10));await page.locator('#close').click();held.send();held=null;await page.waitForTimeout(120);
  assert.equal(await page.locator('dialog').evaluate(e=>e.open),false);assert.equal(polls,0,'Closed account flow must not keep polling');
  assert.equal(requests.filter(x=>x.path.endsWith('/device'))[1].body.remember,false);
  assert.equal(await page.evaluate(()=>Object.keys(localStorage).some(k=>/token|password|credential/.test(k))),false);
  assert.deepEqual(errors,[]);console.log('PASS '+engine+': account linking, remember choice, safe external consent, Nabu/local modes, unconfigured service, provider setup, logout and cancellation');
 }finally{await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
