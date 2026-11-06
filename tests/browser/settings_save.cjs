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
      await page.locator('[data-camera-form]').waitFor({state:'attached'});
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
      let selected = 'mediamtx_janus'; let reject = false; let writes = 0;
      await page.route('**/api/core/engine', async route => {
        if (route.request().method() === 'POST') {
          if (reject) return route.fulfill({status:502,contentType:'application/json',body:JSON.stringify({error:'Could not confirm the saved engine.'})});
          selected = route.request().postDataJSON().core_engine; writes++;
        }
        return route.fulfill({contentType:'application/json',body:JSON.stringify({available:true,running_engine:'mediamtx_janus',core_engine:selected,restart_required:selected!=='mediamtx_janus'})});
      });
      await page.goto(url); await page.locator('[data-camera-form]').waitFor({state:'attached'});
      await page.waitForFunction(() => !document.getElementById('core-engine-select').disabled);
      if (await page.locator('nav').isHidden()) await page.locator('#menu-toggle').click();
      await page.locator('nav [data-page-target="edge-settings"]').click();
      await page.locator('[name="nvr-retention-days"]').fill('29');
      assert(await page.locator('#save-core-engine').isDisabled());
      await page.locator('#core-engine-select').selectOption('edge_rtc');
      assert(!(await page.locator('#save-core-engine').isDisabled()));
      await page.locator('#save-core-engine').click();
      await page.waitForFunction(() => document.getElementById('core-engine-state').textContent.includes('Restart required'));
      assert.equal(writes, 1);
      assert(await page.locator('#save-core-engine').isDisabled());
      assert.equal(await page.locator('[name="nvr-retention-days"]').inputValue(), '29');
      await page.locator('#reload-core-engine').click();
      await page.waitForFunction(() => !document.getElementById('core-engine-select').disabled && document.getElementById('core-engine-select').value==='edge_rtc');
      reject = true;
      await page.locator('#core-engine-select').selectOption('mediamtx');
      await page.locator('#save-core-engine').click();
      await page.waitForFunction(() => document.getElementById('core-engine-state').textContent.startsWith('Could not confirm'));
      assert(!(await page.locator('#save-core-engine').isDisabled()));
      assert.equal(selected, 'edge_rtc');
      assert(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth+1));
      assert.deepEqual(errors, []);
      console.log('PASS engine selector: ' + width + 'px, save/readback, restart state, failed save and independent draft');
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exit(1);});
