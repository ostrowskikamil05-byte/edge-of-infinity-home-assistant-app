const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const url = process.env.EDGE_NVR_TEST_URL || 'http://127.0.0.1:18088';

(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH, args:['--no-sandbox']});
  try {
    for (const mode of ['status-hang', 'post-timeout', 'verify-timeout', 'post-error']) {
      const context = await browser.newContext();
      // Exercise the real AbortController path without waiting 45/15 seconds.
      await context.addInitScript(() => {
        const original = window.setTimeout.bind(window);
        window.setTimeout = (fn, delay, ...args) => original(fn, [45000,15000].includes(delay) ? 300 : delay, ...args);
      });
      const page = await context.newPage();
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.goto(url);
      await page.locator('[data-camera-form]').first().waitFor({state:'attached'});
      await page.locator('nav [data-page-target="edge-settings"]').click();
      const draft = String(30 + ['status-hang','post-timeout','verify-timeout','post-error'].indexOf(mode));
      await page.locator('[name="nvr-retention-days"]').fill(draft);
      if (mode === 'status-hang') await page.route('**/api/presets', () => {});
      let posted = false;
      await page.route('**/api/config', route => {
        if (route.request().method() === 'POST') {
          posted = true;
          if (mode === 'post-timeout') return;
          if (mode === 'post-error') return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Save rejected by test server.'})});
        } else if (mode === 'verify-timeout' && posted) return;
        return route.continue();
      });
      await page.locator('#save-edge-settings').click();
      await page.waitForFunction(() => /Saved and verified\.|timed out|Save rejected/.test(document.getElementById('edge-save-state').textContent));
      assert(!(await page.locator('#save-edge-settings').isDisabled()), mode + ': settings save stayed disabled');
      assert(!(await page.locator('#save-config').isDisabled()), mode + ': camera save stayed disabled');
      assert.equal(await page.locator('[name="nvr-retention-days"]').inputValue(), draft);
      assert.equal(await page.locator('#edge-settings-form').evaluate(el => el.inert), false);
      if (mode !== 'status-hang') assert.equal(await page.locator('[data-page="edge-settings"] [data-unsaved-indicator]').textContent(), 'Unsaved changes');
      assert.deepEqual(errors, []);
      console.log('PASS settings save: ' + mode + ', controls restored and draft retained');
      await context.close();
    }
    for (const width of [1440,820,390,320]) {
      const context = await browser.newContext({viewport:{width,height:1000}});
      const page = await context.newPage();
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/core/engine', () => {throw Error('retired engine selector requested');});
      await page.goto(url);await page.locator('[data-camera-form]').first().waitFor({state:'attached'});
      if(await page.locator('nav').isHidden())await page.locator('#menu-toggle').click();
      await page.locator('nav [data-page-target="edge-settings"]').click();
      assert.equal(await page.locator('#core-engine-select').count(),0);
      assert(await page.locator('#edge-rtc-engine-info').isVisible());
      await page.locator('[name="nvr-retention-days"]').fill('29');
      await page.locator('#save-edge-settings').click();
      await page.waitForFunction(()=>document.getElementById('edge-save-state').textContent.startsWith('Saved and verified.'));
      assert.equal(await page.locator('[name="nvr-retention-days"]').inputValue(),'29');
      assert(await page.evaluate(()=>config.live.engine==='edge_rtc'&&Object.keys(config.live).length===1));
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      assert.deepEqual(errors,[]);
      console.log('PASS native-only settings: '+width+'px, save/readback, retained draft and no engine selector');
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exit(1);});
