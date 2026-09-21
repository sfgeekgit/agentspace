// Read-only browser checks for result documents. No model calls or writes.
// PLAYWRIGHT_BROWSERS_PATH=... NODE_PATH=... AGENTSPACE_PREVIEW_URL=... node scripts/check_results_reader.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');
const base = process.env.AGENTSPACE_PREVIEW_URL || 'http://127.0.0.1:7791';
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}, acceptDownloads:true});
    const errors = [], forbidden = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const request = route.request();
      if (request.method() !== 'GET' || new URL(request.url()).origin !== new URL(base).origin) {
        forbidden.push(request.method()+' '+request.url());
        return route.abort();
      }
      return route.continue();
    });
    const run = 'recess_valdilume_run1';
    await page.goto(base+'/results/'+run);
    await page.getByRole('link',{name:'summary.md',exact:true}).click();
    assert.match(page.url(), /\/view\/summary\.md$/);
    assert.match(await page.locator('#result-extent').textContent(), /Full file shown/);
    await page.locator('.result-prose h2').filter({hasText:'Attributes'}).waitFor();
    assert.match(await page.locator('.result-prose').textContent(), /curiosity: 0/);
    await page.screenshot({path:'/tmp/results-reader-summary.png',fullPage:true});
    await page.getByRole('link',{name:'Source',exact:true}).click();
    assert.match(await page.locator('#result-document').textContent(), /^# recess_valdilume_run1/);
    await page.getByRole('link',{name:'Formatted',exact:true}).click();
    await page.locator('.result-prose').waitFor();
    await page.goto(base+'/results/'+run+'/view/agent_prompts.md');
    assert.match(await page.locator('.result-prose').textContent(), /a13085 — player/);
    const target = await page.locator('#result-jump option').last().getAttribute('value');
    await page.locator('#result-jump').selectOption(target);
    assert.equal(new URL(page.url()).hash, '#'+target);
    await page.goto(base+'/results/'+run+'/view/state.json');
    assert.match(await page.locator('#result-document').textContent(), /\n  "/);
    assert.match(await page.locator('#result-document').textContent(), /bus_departure/);
    await page.setViewportSize({width:390,height:844});
    for (const file of ['summary.md','transcript.md','agent_prompts.md','state.json','transcript.jsonl','manifest.json','dispatch.log']) {
      await page.goto(base+'/results/'+run+'/view/'+file);
      assert.match(await page.locator('#result-extent').textContent(), /nothing truncated/);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth > innerWidth+1), false, 'mobile overflow: '+file);
    }
    await page.goto(base+'/results/'+run+'/view/summary.md');
    await page.screenshot({path:'/tmp/results-reader-mobile.png',fullPage:true});
    // Largest current file stays complete, with all records present and searchable.
    await page.setViewportSize({width:1440,height:1000});
    const largeRun = 'recess_mvp_run1', filename = 'agent_inputs.jsonl';
    const raw = await (await page.request.get(base+'/results/'+largeRun+'/files/'+filename)).text();
    const records = raw.trim().split('\n').map(line=>JSON.parse(line));
    const started = Date.now();
    await page.goto(base+'/results/'+largeRun+'/view/'+filename);
    const elapsed = Date.now()-started;
    assert.equal(await page.locator('.result-record').count(), records.length);
    assert.match(await page.locator('#result-extent').textContent(), /nothing truncated/);
    assert.ok((await page.locator('.result-record').last().textContent()).includes(records.at(-1).agent));
    const event = page.waitForEvent('download');
    await page.getByRole('link',{name:'Download full file',exact:true}).first().click();
    const download = await event;
    assert.equal(download.suggestedFilename(), filename);
    const path = '/tmp/results-reader-download.jsonl';
    await download.saveAs(path);
    assert.equal(fs.readFileSync(path,'utf8'),raw);
    assert.deepEqual(forbidden,[]);
    assert.deepEqual(errors,[]);
    console.log(`RESULT READER: ALL PASS; Markdown, JSON, JSONL, source, navigation, mobile, exact download. Largest file: ${records.length} records, ${elapsed} ms load.`);
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
