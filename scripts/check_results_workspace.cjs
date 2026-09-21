// Regenerates one authorized local report; publication is intercepted, never sent.
// NODE_PATH=/path/to/node_modules AGENTSPACE_PREVIEW_URL=http://127.0.0.1:7790 node scripts/check_results_workspace.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');
const base = process.env.AGENTSPACE_PREVIEW_URL || 'http://127.0.0.1:7790';
const name = process.env.AGENTSPACE_RESULTS_ENV || 'recess_valdilume_run1';
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1400,height:1000}, acceptDownloads:true});
    const errors = [], writes = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      if (req.method() === 'POST') {
        const body = new URLSearchParams(req.postData());
        writes.push(path);
        assert.equal(body.get('name'), name);
        if (path === '/run/results/generate') return route.continue();
        if (path === '/run/results/publish') return route.fulfill({json:{id:'fixture-results-upload'}});
        throw new Error('Unexpected write blocked: '+path);
      }
      if (path === '/runs/fixture-results-upload') return route.fulfill({body:'Publication control verified; no real upload.\n[exit 0]\n'});
      return route.continue();
    });
    await page.goto(base+'/environments');
    const row = page.locator('tr').filter({has:page.locator('a.row-title', {hasText:name})});
    assert.equal(await row.locator('.recess-tag').textContent(), 'Recess');
    await row.getByRole('link', {name:'Results', exact:true}).click();
    await page.locator('#results-status').filter({hasText:'Game: complete'}).waitFor();
    await page.getByRole('button', {name:/^(Re)?generate results$/i}).click();
    await page.locator('#action-dialog').getByRole('button', {name:'Generate results', exact:true}).click();
    await page.waitForEvent('load', {timeout:60000});
    await page.getByRole('link', {name:'Download all (.zip)', exact:true}).waitFor();
    let downloadEvent = page.waitForEvent('download');
    await page.getByRole('link', {name:'Download all (.zip)', exact:true}).click();
    let download = await downloadEvent;
    assert.equal(download.suggestedFilename(), name+'.zip');
    await download.saveAs('/tmp/recess-results-browser.zip');
    assert.equal(fs.readFileSync('/tmp/recess-results-browser.zip').subarray(0,2).toString(), 'PK');
    downloadEvent = page.waitForEvent('download');
    await page.locator('tr').filter({has:page.getByText('agent_prompts.md',{exact:true})}).getByRole('link',{name:'Download',exact:true}).click();
    download = await downloadEvent;
    assert.equal(download.suggestedFilename(), 'agent_prompts.md');
    await download.saveAs('/tmp/recess-results-browser-prompts.md');
    const prompts = fs.readFileSync('/tmp/recess-results-browser-prompts.md','utf8');
    assert.match(prompts, /player/);
    assert.match(prompts, /gm/);
    await page.getByRole('button', {name:'Upload to results GitHub', exact:true}).click();
    assert.match(await page.locator('#dialog-note').textContent(), /separate results repository/);
    await page.locator('#action-dialog').getByRole('button', {name:'Upload to results GitHub', exact:true}).click();
    await page.waitForEvent('load');
    await page.locator('#results-status').filter({hasText:'Game: complete'}).waitFor();
    await page.screenshot({path:'/tmp/recess-results-desktop.png', fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'/tmp/recess-results-mobile.png', fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth > innerWidth+1), false, 'mobile overflow');
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, ['/run/results/generate','/run/results/publish']);
    console.log('RESULTS BROWSER: ALL PASS (report regenerated; ZIP and file downloaded; upload control intercepted)');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
