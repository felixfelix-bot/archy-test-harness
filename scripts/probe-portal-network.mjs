// Diagnostic: log every request the TollGate portal makes, and what comes back.
//
// Answers "why does the portal sit on Loading" with evidence instead of a guess:
// the failing call is named, along with its status and error.
//
// Usage (from the repo root, with this host joined to the paid SSID):
//   node scripts/probe-portal-network.mjs [portal-url] [wait-seconds]
import { chromium } from 'playwright';

const PORTAL = process.argv[2] ?? 'http://192.168.99.1:2050';
const WAIT_S = Number(process.argv[3] ?? 25);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage();

const rows = [];
page.on('requestfailed', (r) =>
  rows.push(['FAILED', r.url(), r.failure()?.errorText ?? '']),
);
page.on('response', (r) => rows.push([r.status(), r.url(), '']));
page.on('pageerror', (e) => rows.push(['JSERR', String(e).slice(0, 160), '']));
// A portal that waits on a relay never issues an HTTP request for it, so a
// request-only view shows a page that "did nothing" and hides the real cause.
page.on('websocket', (ws) => {
  rows.push(['WS-OPEN', ws.url(), '']);
  ws.on('socketerror', (e) => rows.push(['WS-ERR', ws.url(), String(e).slice(0, 120)]));
  ws.on('close', () => rows.push(['WS-CLOSE', ws.url(), '']));
});
page.on('console', (m) => {
  if (m.type() === 'error' || m.type() === 'warning') {
    rows.push([`CONSOLE-${m.type()}`, m.text().slice(0, 160), '']);
  }
});

await page.goto(`${PORTAL}/splash.html`, { waitUntil: 'domcontentloaded' }).catch(() => {});
await page.waitForTimeout(WAIT_S * 1000);

const text = (await page.locator('#root').innerText().catch(() => '')).trim();

console.log(`--- requests (${rows.length}) ---`);
const seen = new Set();
for (const [status, url, err] of rows) {
  const key = `${status} ${url}`;
  if (seen.has(key)) continue;
  seen.add(key);
  console.log(`${String(status).padEnd(7)} ${url}${err ? `  [${err}]` : ''}`);
}

console.log('\n--- rendered text ---');
console.log(text.slice(0, 400));

await browser.close();
