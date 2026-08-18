import { defineConfig } from "vitest/config";

// Store/unit tests only (see docs/superpowers/sdd task-5 brief) — no DOM
// needed, so plain Node is enough and keeps the suite fast.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
    // e2e/ holds the Playwright transcribe->edit->undo->export journey
    // (playwright.config.ts, `npm run test:e2e`) — real browser + real
    // spawned backend, ~60-90s, not part of this fast unit/store suite.
    // `include` above already scopes to src/**, so this is belt-and-braces
    // against vitest ever picking up an e2e/*.spec.ts by accident.
    exclude: ["e2e/**", "node_modules/**"],
  },
});
