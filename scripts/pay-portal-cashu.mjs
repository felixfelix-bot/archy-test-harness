// Pay the TollGate portal with real testnut ecash, as a real client on the paid
// SSID, and record whether access is actually granted.
//
// The claim this produces is NOT "the portal rendered" — it is "this client paid
// and this client's traffic is now permitted", measured by fetching a URL
// through the paid interface before and after.
import { chromium } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

const PORTAL = process.env.TOLLGATE_PORTAL_URL ?? 'http://192.168.99.1:2050';
const TOKEN = readFileSync(process.env.TOLLGATE_TOKEN_FILE, 'utf8').trim();
const IFACE_IP = process.env.TOLLGATE_CLIENT_IP ?? '192.168.99.158';

/** Can this client reach the internet through the paid interface? */
function internetViaPaid() {
  try {
    return execFileSync('curl', [
      '-s', '-m', '8', '-o', '/dev/null', '--interface', IFACE_IP,
      '-w', '%{http_code}', 'http://example.com/',
    ], { encoding: 'utf8' }).trim();
  } catch {
    return '000';
  }
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const context = await browser.newContext({
  recordVideo: { dir: 'results/tollgate/payment/video', size: { width: 1024, height: 768 } },
});
const page = await context.newPage();

const before = internetViaPaid();
console.log(`before payment: example.com via ${IFACE_IP} -> ${before}`);

await page.goto(PORTAL, { waitUntil: 'domcontentloaded', timeout: 45000 });
await page.waitForTimeout(2500);

// Pick the offer (10 SATS / 1 min) so the portal knows what is being bought.
const offer = page.locator('button', { hasText: /SATS?/i }).first();
await offer.click({ timeout: 15000 });

await page.locator('#cashu-token').fill(TOKEN, { timeout: 15000 });
await page.screenshot({ path: 'results/tollgate/payment/01-token-entered.png' });

const submit = page
  .locator('button', { hasText: /pay|purchase|connect|buy|redeem|submit/i })
  .first();
await submit.click({ timeout: 15000 });

// Wait for the portal to stop being busy rather than for a fixed time.
await page.waitForTimeout(12000);
await page.screenshot({ path: 'results/tollgate/payment/02-after-submit.png' });
const text = (await page.locator('#root').innerText()).replace(/\n{2,}/g, '\n');

const after = internetViaPaid();
console.log(`after payment:  example.com via ${IFACE_IP} -> ${after}`);
console.log('--- portal says ---');
console.log(text.slice(0, 600));

await context.close(); // flushes the video
await browser.close();

console.log(JSON.stringify({ before, after, token_len: TOKEN.length }));
process.exit(after === '200' ? 0 : 3);
