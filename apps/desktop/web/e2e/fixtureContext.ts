// Read side of the env vars global-setup.ts writes — see that file's
// `FIXTURE_WAV_ENV`/`WORK_DIR_ENV` doc comment for why plain `process.env`
// is reliable here (Playwright forks each worker with a snapshot of
// `process.env` taken after globalSetup returns).

import { FIXTURE_WAV_ENV, WORK_DIR_ENV } from "./global-setup";

export interface E2EFixtureContext {
  fixtureWavPath: string;
  workDir: string;
}

export function readFixtureContext(): E2EFixtureContext {
  const fixtureWavPath = process.env[FIXTURE_WAV_ENV];
  const workDir = process.env[WORK_DIR_ENV];
  if (!fixtureWavPath || !workDir) {
    throw new Error(
      `${FIXTURE_WAV_ENV}/${WORK_DIR_ENV} are not set — this spec must run ` +
        "under Playwright's globalSetup (e2e/global-setup.ts), not directly.",
    );
  }
  return { fixtureWavPath, workDir };
}
