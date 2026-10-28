// Dump the portal's interactive surface so a token can be submitted by name
// rather than by guessing selectors.
import { chromium } from '@playwright/test';

const PORTAL = process.env.TOLLGATE_PORTAL_URL ?? 'http://192.168.99.1:2050';

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage();
await page.goto(PORTAL, { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForTimeout(3000);

const info = await page.evaluate(() => {
  const pick = (el, i) => ({
    i,
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type'),
    placeholder: el.getAttribute('placeholder'),
    id: el.id || null,
    name: el.getAttribute('name'),
    text: (el.innerText || '').trim().slice(0, 40),
    aria: el.getAttribute('aria-label'),
    visible: !!(el.offsetWidth || el.offsetHeight),
  });
  return {
    inputs: [...document.querySelectorAll('input,textarea')].map(pick),
    buttons: [...document.querySelectorAll('button,[role=button],a')]
      .map(pick)
      .filter((b) => b.visible)
      .slice(0, 25),
    text: document.body.innerText.replace(/\n{2,}/g, '\n').slice(0, 1200),
  };
});

console.log(JSON.stringify(info, null, 2));
await browser.close();
