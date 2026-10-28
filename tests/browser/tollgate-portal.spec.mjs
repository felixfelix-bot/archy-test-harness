// SCOPE OF WHAT A PASS MEANS
// --------------------------
// This spec proves the portal, as served by a real 0.6.0 router, renders a
// live offer and completes at least one 200 against its backend on :2121.
// It does NOT by itself prove a *paying-zone client* can reach :2121 — that
// is decided by nftables/nDS and is asserted structurally in
// tests/e2e/test_tollgate_paid_bridge_api.py.
//
// Do not run this through an SSH tunnel to the router and call it evidence:
// NoDogSplash refuses non-client sources, answering 500 for the portal and
// making the backend call untestable. Observed on a GL-MT3000: every path
// (/, /splash.html, /splash.html?redir=...) returned 500 from a tunnel while
// an associated client got 200. The run must originate from a real client on
// the paid SSID.
// TollGate captive portal — proves the paid portal served by the router renders
// and is usable, with a video a human can watch instead of taking a report's word.
//
// Why the URL rewriting: NoDogSplash answers on the paid bridge and redirects
// every request to its canonical host (http://192.168.99.1:2050/splash.html),
// because that is the address a CUSTOMER's browser is expected to reach. A test
// driving the portal through an SSH tunnel cannot resolve that address, so the
// redirect target is rewritten onto the tunnel. The rewrite is transport only —
// the bytes, and therefore the assertion, are the router's.
import { test, expect } from '@playwright/test';

// The portal redirects to its canonical host; the browser's resolver is pointed
// at the tunnel for that host in playwright.tollgate.config.mjs
// (--host-resolver-rules), so no request rewriting is needed here and the bytes
// asserted on are genuinely the router's.
const PORTAL = process.env.TOLLGATE_PORTAL_URL ?? 'http://192.168.99.1:2050';

test.describe('TollGate captive portal', () => {
  test('renders the portal the router actually serves', async ({ page }) => {
    // The SPA fetches its offer from the backend on :2121. A price can be
    // rendered from configuration alone, so a rendered number does not prove
    // the backend answered. Record the actual responses and require a 200.
    const apiCalls = [];
    page.on('response', (res) => {
      const url = res.url();
      if (/:2121\b/.test(url) || url.includes(':2121/')) {
        apiCalls.push({ url, status: res.status() });
      }
    });
    page.on('requestfailed', (req) => {
      if (req.url().includes(':2121')) {
        apiCalls.push({ url: req.url(), status: 0,
                        failure: req.failure()?.errorText || 'failed' });
      }
    });

    const resp = await page.goto(PORTAL, { waitUntil: 'domcontentloaded' });
    expect(resp, 'portal must answer').not.toBeNull();

    // The document title is the product's own claim about what this page is.
    await expect(page).toHaveTitle(/Tollgate Captive Portal/i);

    // The SPA must mount: an empty #root means the bundle failed to execute,
    // which a 200 status alone would not reveal.
    const root = page.locator('#root');
    await expect(root).toBeAttached({ timeout: 15000 });
    await page.waitForFunction(
      () => {
        const el = document.querySelector('#root');
        return el && el.children.length > 0;
      },
      { timeout: 20000 },
    );

    const text = (await root.innerText()).trim();
    expect(text.length, 'portal mounted but rendered no text').toBeGreaterThan(0);

    // A portal that MOUNTS but cannot reach its own backend renders a branded
    // error banner and still returns HTTP 200 with no console error. Measured:
    // without a reachable mint the page shows "#TG003 / Could not fetch
    // TollGate information". Asserting only "it rendered" passes on that
    // broken state, so the failure is named here explicitly.
    const errorCodes = ['TG003', 'Could not fetch TollGate information'];
    for (const marker of errorCodes) {
      expect(
        text.includes(marker),
        `portal rendered its own error state (${marker}) — it is up but its ` +
          `backend/relay is not healthy. Rendered text:\n${text}`,
      ).toBe(false);
    }

    // And it must offer something a customer can actually act on. "Loading"
    // and a footer link both satisfy a loose regex, so wait for the actual
    // offer to render and assert a price, which only comes from the backend.
    await expect(page.getByText(/\d+\s*SAT(S)?/i).first()).toBeVisible({
      timeout: 45000,
    });
    const settled = (await root.innerText()).trim();
    expect(
      /SAT(S)?/i.test(settled),
      `portal never rendered a price. Rendered text:\n${settled}`,
    ).toBe(true);
    expect(
      /pay|purchase|connect|buy/i.test(settled),
      `portal shows no payment/access affordance. Rendered text:\n${settled}`,
    ).toBe(true);

    // Finally: the portal must have actually reached its backend. Without
    // this the suite blesses a portal that renders a configured price while
    // every real API call is dropped - the exact defect that hid on hardware.
    const ok = apiCalls.filter((c) => c.status === 200);
    expect(
      ok.length,
      `portal rendered an offer but no successful call to the backend on :2121 ` +
        `(the number may be a configured default, not a live quote).\n` +
        `observed: ${JSON.stringify(apiCalls, null, 2)}`,
    ).toBeGreaterThan(0);
  });

  test('serves its SPA bundle without a console error', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(`${PORTAL}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });

    await page.waitForTimeout(2000);
    // A bundling/CSP breakage shows up here and nowhere else.
    expect(errors, `portal threw during load: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
