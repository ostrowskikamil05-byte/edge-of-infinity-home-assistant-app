// Real panel: automatic HA/browser locale, manual choices, forms and media DOM.
const {chromium,webkit}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const wk=process.env.EDGE_TEST_BROWSER==='webkit',browser=await(wk?webkit:chromium).launch({headless:true,executablePath:(wk?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:wk?[]:['--no-sandbox']}),base=process.env.EDGE_NVR_TEST_URL||'http://127.0.0.1:18088';
 const labels={en:['Recordings','Settings'],pl:['Nagrania','Ustawienia'],de:['Aufnahmen','Einstellungen'],fr:['Enregistrements','Paramètres'],es:['Grabaciones','Ajustes'],it:['Registrazioni','Impostazioni'],uk:['Записи','Налаштування']};
 try{
  for(const [locale,language,width]of [['pl-PL','pl',390],['en-GB','en',1440],['de-DE','de',820],['nl-NL','en',320]]){
   const context=await browser.newContext({locale,viewport:{width,height:900}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(15000);
   await page.goto(base);await page.waitForFunction(()=>document.querySelector('[name="camera-0-name"]'));
   await page.waitForFunction(lang=>document.documentElement.lang===lang,language);
   assert.equal(await page.locator('nav [data-page-target="nvr"] .nav-label').textContent(),labels[language][0]);
   if(locale==='pl-PL'){
    await page.evaluate(()=>showPage('camera-settings'));
    await page.locator('[name="camera-0-name"]').fill('Settings');await page.locator('[name="camera-0-password"]').fill('Private-test-<tag>-ąć');
    await page.evaluate(()=>showPage('edge-settings'));
    const state=await page.evaluate(()=>({config:JSON.stringify(config),dirty:configDirty}));assert(state.dirty);
    await page.evaluate(()=>{window.savedMedia=[...document.querySelectorAll('video,canvas,iframe')];window.savedForm=document.querySelector('[data-camera-form]');const raw=document.createElement('section');raw.dataset.logEvents='';raw.id='language-raw-fixture';raw.textContent='Recordings';document.body.append(raw);const code=document.createElement('pre');code.id='language-code-fixture';code.textContent='Settings';document.body.append(code);});
    for(const [code,[recordings,settings]]of Object.entries(labels)){
     await page.evaluate(()=>uiShell.preferences());await page.locator('#edge-language').selectOption(code);
     await page.waitForFunction(code=>document.documentElement.lang===code,code);
     assert.equal(await page.locator('nav [data-page-target="nvr"] .nav-label').textContent(),recordings);
     assert.equal(await page.locator('[data-page="edge-settings"] h1').textContent(),await page.evaluate(()=>EdgeI18n.text('System & recorder')));
     await page.locator('#ui-app-preferences [data-ui-close]').click();
     assert.equal(await page.locator('[name="camera-0-name"]').inputValue(),'Settings');
     assert.equal(await page.locator('[data-camera-summary]').first().textContent(),'Settings');
     assert.equal(await page.locator('[name="camera-0-password"]').inputValue(),'Private-test-<tag>-ąć');
     assert(await page.evaluate(()=>savedForm===document.querySelector('[data-camera-form]')&&savedMedia.every(n=>n.isConnected)),'language replaced form/media elements');
     assert.equal(await page.locator('#language-raw-fixture').textContent(),'Recordings');assert.equal(await page.locator('#language-code-fixture').textContent(),'Settings');
     assert.equal(await page.evaluate(()=>JSON.stringify(config)),state.config);assert(await page.evaluate(()=>configDirty));
     assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'localized phone UI overflows: '+code);
    }
    await page.reload();await page.waitForFunction(()=>document.documentElement.lang==='uk');
    await page.evaluate(()=>showPage('edge-settings'));assert.equal(await page.locator('#edge-language').inputValue(),'uk');
    await page.evaluate(()=>uiShell.preferences());await page.locator('#edge-language').selectOption('auto');await page.locator('#ui-app-preferences [data-ui-close]').click();await page.waitForFunction(()=>document.documentElement.lang==='pl');
   }
   assert.deepEqual(errors,[]);console.log(`PASS ${wk?'webkit':'chromium'} ${locale}: automatic ${language}, localized navigation, manual persistence, private form values and existing media nodes`);await context.close();
  }
  const context=await browser.newContext({locale:'pl-PL'}),page=await context.newPage();
  await page.goto(base);await page.evaluate(base=>{document.body.replaceChildren();const host=document.createElement('home-assistant');host.hass={locale:{language:'fr-FR'}};document.body.append(host);const frame=document.createElement('iframe');frame.src=base;frame.id='ha-language-panel';document.body.append(frame);},base);
  const frame=page.frameLocator('#ha-language-panel');await frame.locator('html[lang="fr"]').waitFor();assert.equal(await frame.locator('nav [data-page-target="nvr"] .nav-label').textContent(),'Enregistrements');
  // A preference changed by another same-origin HA page reaches the panel.
  await page.evaluate(()=>localStorage.setItem('edge.interface.language','de'));await frame.locator('html[lang="de"]').waitFor();
  await page.evaluate(()=>localStorage.removeItem('edge.interface.language'));await frame.locator('html[lang="fr"]').waitFor();
  await page.evaluate(()=>{document.querySelector('home-assistant').hass={locale:{language:'it-IT'}};document.querySelector('iframe').contentWindow.dispatchEvent(new Event('focus'));});await frame.locator('html[lang="it"]').waitFor();
  console.log(`PASS ${wk?'webkit':'chromium'}: HA language takes priority over browser, cross-page manual preference and return to automatic`);await context.close();
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
