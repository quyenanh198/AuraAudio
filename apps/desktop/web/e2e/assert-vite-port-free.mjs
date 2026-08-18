#!/usr/bin/env node
// Runs as the first step of playwright.config.ts's `webServer.command`,
// BEFORE `vite` itself starts. Playwright starts `webServer` concurrently
// with (observed empirically: actually before) `globalSetup` finishes its
// own port check — see global-setup.ts's `assertPortsFree`, which
// therefore only gates port 8317 (the one this test suite alone owns) and
// leaves 5173 to this script, so a stale process on 5173 fails fast with a
// clear, pkill-guided message instead of racing globalSetup's check or
// falling through to Vite's own less specific "Port 5173 is already in
// use" (from `--strictPort`, kept as a second line of defense in the
// `npm run dev` command that follows this script).

import net from "node:net";

const PORT = 5173;

function isPortFree(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port, timeout: 500 });
    socket.once("connect", () => {
      socket.destroy();
      resolve(false);
    });
    socket.once("timeout", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => {
      resolve(true);
    });
  });
}

const free = await isPortFree(PORT);
if (!free) {
  process.stderr.write(
    `e2e/assert-vite-port-free.mjs: port ${PORT} already in use — the ` +
      "edit-journey test's Vite dev server needs it free. A previous run " +
      "likely left a process behind. Try:\n" +
      "  pkill -f vite\n" +
      "then re-run `npm run test:e2e`.\n",
  );
  process.exit(1);
}
