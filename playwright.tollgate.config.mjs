// Playwright config for the TollGate portal evidence run.
//
// Video is recorded for EVERY test, passing or failing, because the artifact's
// job is to let a human confirm the claim without re-running anything.
import { defineConfig } from '@playwright/test';

const PORTAL = process.env.TOLLGATE_PORTAL_URL ?? 'http://localhost:12050';

export default defineConfig({
  testDir: './tests/browser',
  testMatch: '**/tollgate-portal.spec.mjs',
  timeout: 60000,
  retries: 0,
  workers: 1,
  reporter: [
    ['list'],
    ['json', { outputFile: 'results/tollgate/browser/results.json' }],
  ],
  outputDir: 'results/tollgate/browser/artifacts',
  use: {
    baseURL: PORTAL,
    headless: true,
    ignoreHTTPSErrors: true,
    // When driving the portal through an SSH tunnel (TOLLGATE_TUNNEL=1), the
    // browser must be told where the router's canonical host really is, because
    // NoDogSplash redirects every request to it. When this host is ITSELF a
    // client on the paid SSID, no mapping is wanted — the address routes.
    launchOptions: {
      args: process.env.TOLLGATE_TUNNEL === '1'
        ? ['--host-resolver-rules=MAP 192.168.99.1 [::1]', '--no-sandbox']
        : ['--no-sandbox'],
    },
    screenshot: 'on',
    // 'retain-on-failure' would delete the video of a PASSING run — the exact
    // artifact the operator asked for.
    video: 'on',
    actionTimeout: 15000,
    navigationTimeout: 30000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
// NOTE (measured): the portal SPA calls its backend on :2121, not only the page
// on :2050. A tunnel forwarding BOTH is required or the page renders its own
// "#TG003 Could not fetch TollGate information" error — which looks like a
// product failure and is really a missing forward. Verified: forwarding only
// :2050 gives TG003; forwarding :2050 and :2121 gives the full payment UI.
