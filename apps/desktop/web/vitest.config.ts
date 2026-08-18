import { defineConfig } from "vitest/config";

// Store/unit tests only (see docs/superpowers/sdd task-5 brief) — no DOM
// needed, so plain Node is enough and keeps the suite fast.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
