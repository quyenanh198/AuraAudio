// Global setup for the transcribe->edit->undo->export Playwright journey
// (e2e/edit-journey.spec.ts). Boots the REAL FastAPI backend (the same
// entrypoint the packaged desktop app runs, apps/desktop/run_backend.py)
// against a throwaway SQLite DB in a fresh temp dir, and generates the
// short guitar-pluck WAV fixture the spec uploads — both BEFORE any
// browser or Vite dev server (the Playwright `webServer` option, config'd
// separately in playwright.config.ts) touches anything.
//
// Per the Playwright global-setup contract, returning a function from this
// module's default export registers it as the matching global TEARDOWN —
// run once after every test file in the run has finished. Everything this
// setup creates (the backend child process, the temp dir) is torn down
// there, not in a separate globalTeardown file, specifically so the
// backend's ChildProcess handle can be captured in a closure instead of
// serialized across files.
//
// Env var contract for `run_backend.py` (verified against
// apps/desktop/tests/test_schema_init.py and test_cors_scope.py, this
// project's own existing tests that boot the same entrypoint): AURA_DATA_DIR
// is a plain directory path; DATABASE_URL is `sqlite:///` (three slashes)
// immediately followed by an ABSOLUTE filesystem path — since an absolute
// path itself starts with `/`, the two concatenate into the familiar
// four-slash `sqlite:////...` form. Both must point under the SAME temp
// dir subtree so a torn-down run leaves nothing behind.

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// `apps/desktop/web/package.json` sets `"type": "module"`, so this file
// runs as real ESM under Node (Playwright's loader does not rewrite it to
// CJS) — `__dirname` is not defined in that mode, unlike a bundled/CJS
// context. `import.meta.url` is the ESM-native equivalent.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../../../..");
const BACKEND_PORT = 8317;
const HEALTHZ_URL = `http://127.0.0.1:${BACKEND_PORT}/healthz`;
const HEALTHZ_TIMEOUT_MS = 90_000;
const HEALTHZ_POLL_MS = 500;
const BACKEND_SHUTDOWN_GRACE_MS = 5_000;

/** Env vars this module sets for the spec to read (see e2e/fixtureContext.ts).
 * Playwright forks each test-file worker with `{...process.env,
 * ...extraEnv}` (verified against the installed 1.62.1 runner,
 * `playwright/lib/runner/index.js`'s `ProcessHost.startRunner`) at DISPATCH
 * time, which is always after globalSetup has returned — so plain
 * `process.env` writes made here are visible in every worker/spec. */
export const FIXTURE_WAV_ENV = "E2E_FIXTURE_WAV_PATH";
export const WORK_DIR_ENV = "E2E_WORK_DIR";

async function isPortFree(port: number): Promise<boolean> {
  // A bound port answers *something* on connect (even a RST) faster than a
  // real timeout; an unbound one just hangs until ECONNREFUSED. `fetch`
  // against a plain TCP port with no HTTP server would hang instead of
  // erroring quickly, so this uses a raw socket connect instead.
  const net = await import("node:net");
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port, timeout: 500 });
    socket.once("connect", () => {
      socket.destroy();
      resolve(false); // something answered -> port is taken
    });
    socket.once("timeout", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => {
      resolve(true); // ECONNREFUSED etc. -> nothing listening
    });
  });
}

/** Only gates port 8317 (the backend this module itself spawns) — NOT
 * 5173. Playwright starts the `webServer` (Vite, port 5173) concurrently
 * with globalSetup, observed empirically to usually already be up and
 * bound by the time this runs, so checking 5173 here produces a false
 * "already in use" against Playwright's own, expected server. Port 5173 is
 * instead guarded by `e2e/assert-vite-port-free.mjs`, which runs as the
 * first step of the `webServer.command` itself, strictly before `vite`
 * binds anything. */
async function assertPortsFree(): Promise<void> {
  if (await isPortFree(BACKEND_PORT)) return;
  throw new Error(
    `e2e/global-setup.ts: port ${BACKEND_PORT} already in use — the ` +
      "edit-journey test needs it free for the real backend this module " +
      "spawns. A previous run likely left a process behind. Try:\n" +
      "  pkill -f run_backend.py\n" +
      "then re-run `npm run test:e2e`.",
  );
}

function generateFixtureWav(fixtureWavPath: string): void {
  // Short (2s) so basic-pitch transcription stays on the fast end of its
  // ~60-90s budget. write_guitar_pluck_wav is packages/test_fixtures' own
  // deterministic, rights-free guitar-pluck synthesizer (real recordings
  // aren't needed and wouldn't be reproducible) — four open-string notes
  // (E2 A2 D3 G3), one per 0.5s, verified to give basic-pitch clean onsets.
  const script = [
    "from pathlib import Path",
    "from test_fixtures.generate import write_guitar_pluck_wav",
    `write_guitar_pluck_wav(Path(${JSON.stringify(fixtureWavPath)}), duration_s=2.0)`,
  ].join("\n");
  const result = spawnSync("uv", ["run", "--package", "test-fixtures", "python", "-c", script], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
  });
  if (result.status !== 0) {
    throw new Error(
      `e2e/global-setup.ts: fixture WAV generation failed (exit ${result.status}).\n` +
        `stdout: ${result.stdout}\nstderr: ${result.stderr}`,
    );
  }
}

async function waitForHealthz(): Promise<void> {
  const deadline = Date.now() + HEALTHZ_TIMEOUT_MS;
  let lastError: unknown;
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(HEALTHZ_URL);
      if (resp.ok) return;
    } catch (err) {
      lastError = err;
    }
    await new Promise((resolve) => setTimeout(resolve, HEALTHZ_POLL_MS));
  }
  throw new Error(
    `e2e/global-setup.ts: backend never answered ${HEALTHZ_URL} within ` +
      `${HEALTHZ_TIMEOUT_MS}ms. Last error: ${String(lastError)}`,
  );
}

/** Sends `signal` to the whole process group `backend` (spawned with
 * `detached: true`, so it is its own group leader) — `uv run` execs a
 * child `python` process, and only killing the GROUP (negative pid, POSIX
 * convention) reliably reaps both instead of orphaning the real backend
 * when just the `uv` wrapper is signaled. */
function killProcessGroup(backend: ChildProcess, signal: NodeJS.Signals): void {
  if (backend.pid === undefined) return;
  try {
    process.kill(-backend.pid, signal);
  } catch {
    // Already dead — nothing to signal.
  }
}

async function waitForExit(backend: ChildProcess, timeoutMs: number): Promise<boolean> {
  if (backend.exitCode !== null || backend.signalCode !== null) return true;
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    backend.once("exit", () => {
      clearTimeout(timer);
      resolve(true);
    });
  });
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  await assertPortsFree();

  const workDir = mkdtempSync(path.join(tmpdir(), "aura-e2e-"));
  const fixtureWavPath = path.join(workDir, "fixture-guitar.wav");
  const dataDir = path.join(workDir, "data");
  const dbPath = path.join(dataDir, "aura.db");

  generateFixtureWav(fixtureWavPath);

  const backend = spawn("uv", ["run", "python", "apps/desktop/run_backend.py"], {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      AURA_DATA_DIR: dataDir,
      DATABASE_URL: `sqlite:///${dbPath}`,
    },
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
  });

  let backendOutput = "";
  backend.stdout?.on("data", (chunk) => {
    backendOutput += String(chunk);
  });
  backend.stderr?.on("data", (chunk) => {
    backendOutput += String(chunk);
  });

  const exitedEarly = new Promise<never>((_resolve, reject) => {
    backend.once("exit", (code, signal) => {
      reject(
        new Error(
          `e2e/global-setup.ts: backend process exited early (code=${code} ` +
            `signal=${signal}) before healthz responded.\nOutput:\n${backendOutput}`,
        ),
      );
    });
  });

  try {
    await Promise.race([waitForHealthz(), exitedEarly]);
  } catch (err) {
    killProcessGroup(backend, "SIGKILL");
    rmSync(workDir, { recursive: true, force: true });
    throw err;
  }

  process.env[FIXTURE_WAV_ENV] = fixtureWavPath;
  process.env[WORK_DIR_ENV] = workDir;

  return async () => {
    killProcessGroup(backend, "SIGTERM");
    const exited = await waitForExit(backend, BACKEND_SHUTDOWN_GRACE_MS);
    if (!exited) killProcessGroup(backend, "SIGKILL");
    rmSync(workDir, { recursive: true, force: true });
  };
}
