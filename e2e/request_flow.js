// Browser run-through of the demo flow. Needs a running portal seeded with the OSS demo data
// (scripts/seed_demo.py) plus a customer user a@demo.example (customer 1), password demo-password-123.
// Run: NODE_PATH=$(npm root -g) node e2e/request_flow.js [shots-dir]     (PORTAL_URL to override the URL)
// Customer enters 10 samples: sets up row 1 and two analyses, pastes 9 IDs, selects two samples and gives
// them an extra analysis and Next Day, gives sample 3 a free-text chemical, uses Ctrl+D, then submits.
const { chromium } = require('playwright');
const SHOTS = process.argv[2] || 'screenshots';
require('fs').mkdirSync(SHOTS, { recursive: true });
const BASE = process.env.PORTAL_URL || 'http://127.0.0.1:8000';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = []; page.on('pageerror', e => errors.push(String(e)));
  await page.goto(`${BASE}/login`);
  await page.fill('#email', 'a@demo.example'); await page.fill('#password', 'demo-password-123');
  await page.click('button[type=submit]');
  await page.goto(`${BASE}/requests/new`);
  await page.waitForSelector('#request_type-1');
  await page.check('#request_type-1');
  const row = (i) => page.locator('#samples tr').nth(i);
  await row(0).locator('input[data-f=sample_name]').fill('BATCH-01');
  await row(0).locator('input[data-f=chemical_name]').fill('Chemical 01');
  await row(0).locator('select[data-f=processing_time]').selectOption({ label: 'Three Days' });
  console.log('bar (none selected):', await page.textContent('#selection-count'));
  for (const name of ['36 Elements', 'pH']) await page.getByLabel(name, { exact: true }).check();
  await page.click('#analysis-done');
  console.log('collapsed:', await page.textContent('#analysis-collapsed'));
  await page.fill('#paste-ids', Array.from({ length: 9 }, (_, i) => `BATCH-${String(i + 2).padStart(2, '0')}`).join('\n'));
  await page.click('#add-pasted');
  console.log('rows:', await page.locator('#samples tr').count());
  // select rows 9 and 10, add 4 Anions to just those, set Next Day
  await row(8).locator('input[data-select]').check(); await row(9).locator('input[data-select]').check();
  console.log('bar:', await page.textContent('#selection-count'), '|', await page.textContent('#analysis-legend'));
  await page.getByLabel('4 Anions', { exact: true }).check();
  await page.selectOption('#bulk-processing-time', { label: 'Next Day' });
  await page.locator('#samples-section').screenshot({ path: `${SHOTS}/15_matrix_selected.png` });
  await page.click('#bulk-clear');
  console.log('collapsed after clear:', await page.isVisible('#analysis-collapsed'));
  await row(2).locator('input[data-f=chemical_name]').fill('IPA, semiconductor grade');   // not in SLIM
  await row(2).locator('[data-only]').click();                                 // select only sample 3
  await page.getByLabel('pH', { exact: true }).uncheck();
  console.log('legend:', await page.textContent('#analysis-legend'));
  await page.click('#bulk-clear');
  const n3 = row(2).locator('input[data-f=additional_notes]');
  await row(1).locator('input[data-f=additional_notes]').fill('keep cold');
  await n3.focus(); await page.keyboard.press('Control+d');
  console.log('row3 notes after Ctrl+D:', await n3.inputValue());
  await page.locator('#samples-section').screenshot({ path: `${SHOTS}/16_matrix.png` });
  for (const [id, v] of [['#customer_contact', 'Jane'], ['#customer_phone', '555'], ['#results_to', 'j@a.example'],
                         ['#invoice_to', 'ap@a.example'], ['#po_number', 'PO-1']]) await page.fill(id, v);
  await page.click('#submit-button');
  await page.waitForURL(/\/requests\/TR\d+/);
  console.log('submitted', page.url());
  const detail = await page.textContent('main');
  console.log('detail ok:', detail.includes('BATCH-10') && detail.includes('4 Anions'));
  console.log('JS errors:', errors.length ? errors : 'none');
  await browser.close();
})().catch(e => { console.error('E2E FAILED:', e.message); process.exit(1); });
