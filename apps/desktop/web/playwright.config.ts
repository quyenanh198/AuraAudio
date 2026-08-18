import { defineConfig } from "@playwright/test";

// End-to-end regression config for the transcribe -> edit -> undo -> export
// journey (e2e/edit-journey.spec.ts). Deliberately separate from
// vitest.config.ts's unit/store suite: this drives a real browser against a
// real spawned backend (see e2e/global-setup.ts) and a real Vite dev server
// below, exercising basic-pitch transcription end to end — slow (~60-90s)
// and not part of `npm test`/`make test`. Run via `npm run test:e2e`
// (apps/desktop web) or `make e2e-web` (repo root).
//
// Offline-only, no browser download: this sandbox pre-stages Chromium at
// PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers, but the bare `chromium` path
// under that root is a symlink to a revision this installed `playwright`
// version doesn't expect (see docs/superpowers/SESSION-HANDOFF.md's
// "Environment gotchas" — Playwright was already a dependency here for
// sub-project 3's OSMD spike verification, and that gotcha was found then).
// `launchOptions.executablePath` below pins the exact concrete binary
// instead of relying on PLAYWRIGHT_BROWSERS_PATH auto-resolution, so no
// `npx playwright install` / download is ever attempted.
const CHROMIUM_EXECUTABLE = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";

const VITE_PORT = 5173;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  globalSetup: "./e2e/global-setup.ts",
  // One real backend + one project per run (see global-setup.ts) — tests
  // are written to run in one serial describe block already, but pinning
  // workers to 1 and disabling parallelism keeps a future second spec file
  // from racing the same backend/port.
  fullyParallel: false,
  workers: 1,
  // No retries: a flaky-looking failure here should be investigated, not
  // silently swallowed by a retry — this file's own two-consecutive-runs
  // requirement (see the report) is how flakiness gets proven away instead.
  retries: 0,
  forbidOnly: !!process.env.CI,
  // Transcription alone is ~60-90s (real basic-pitch/tensorflow inference,
  // no mocking) — generous per-test and per-action timeouts throughout so a
  // slow CI/sandbox machine doesn't false-fail on real, expected latency.
  timeout: 5 * 60 * 1000,
  expect: {
    timeout: 15_000,
  },
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${VITE_PORT}`,
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: {
      executablePath: CHROMIUM_EXECUTABLE,
    },
  },
  webServer: {
    // Playwright starts `webServer` concurrently with (observed: actually
    // before) `globalSetup` completes its own port check, so gating port
    // 5173 there would race this server's own startup — checked here
    // instead, via a tiny script that runs before `vite` itself (see its
    // own header comment). `--strictPort` stays as a second line of
    // defense so a stale process squatting on 5173 still fails loudly
    // instead of Vite silently drifting to 5174 and the test hitting the
    // wrong origin.
    command: `node e2e/assert-vite-port-free.mjs && npm run dev -- --port ${VITE_PORT} --strictPort`,
    url: `http://localhost:${VITE_PORT}`,
    reuseExistingServer: false,
    timeout: 30_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
