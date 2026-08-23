// Auto-install for the app's external dependencies (ffmpeg, yt-dlp),
// invoked from the deps banner's "Install automatically" button.
//
// The actual install runs on the TAURI (Rust) side --
// apps/desktop/src-tauri/src/install.rs's `install_dependency` command --
// because spawning a package-manager process is a privileged UI action,
// not something the bundled Python backend should be trusted to do over
// its loopback HTTP port. This module is the thin state machine wrapping
// that one `invoke()` call: installing -> rechecking -> ok/failed.
//
// "rechecking" is the user's explicit ask (see the deps-autoinstall task):
// the version check must run AFTER the install completes, not just be
// shown as a command beforehand. That recheck reuses the SAME `deps` store
// the banner already renders from (`deps.recheck()`), rather than trusting
// the install command's own exit code as proof the binary now works --
// mirrors the real-world Windows trap this whole feature exists to fix
// (winget reports success, but the already-running app's PATH is stale
// until restart, so the dependency can still resolve as missing).
//
// `@tauri-apps/api/core` (unlike the `@tauri-apps/plugin-*` packages
// saveExport.ts dynamically imports) is safe to import statically outside
// Tauri -- `isTauri()` just returns `false` there, same assumption
// saveExport.ts already makes for its own top-level import of it.

import { invoke, isTauri as isTauriRuntime } from "@tauri-apps/api/core";
import { get, writable } from "svelte/store";

import { deps, type DepName } from "./deps";
import type { DependencyStatus, SystemDepsResponse } from "./types";

export function isTauri(): boolean {
  return isTauriRuntime();
}

/** Mirrors `apps/desktop/src-tauri/src/install.rs`'s `InstallOutcome`
 * verbatim (serde `rename_all = "snake_case"`). */
export type InstallOutcome = "success" | "failed" | "unsupported" | "brew_missing";

/** Mirrors `install.rs`'s `InstallDependencyResult` (serde `rename_all =
 * "camelCase"` on the struct, so `exit_code`/`output_tail` arrive as
 * `exitCode`/`outputTail`). */
export interface InstallDependencyResult {
  outcome: InstallOutcome;
  exitCode: number | null;
  outputTail: string;
}

export type InstallPhase = "idle" | "installing" | "rechecking" | "ok" | "failed";

export interface InstallState {
  phase: InstallPhase;
  outcome: InstallOutcome | null;
  outputTail: string | null;
  /** The dependency's reported version, populated only once the
   * post-install recheck confirms it's actually found -- this is the
   * "show the found version AFTER install" requirement's data source. */
  version: string | null;
}

const IDLE_STATE: InstallState = { phase: "idle", outcome: null, outputTail: null, version: null };

function isInstallDependencyResult(value: unknown): value is InstallDependencyResult {
  return (
    typeof value === "object" &&
    value !== null &&
    "outcome" in value &&
    typeof (value as { outcome: unknown }).outcome === "string" &&
    "outputTail" in value
  );
}

function statusFor(dep: DepName, detail: SystemDepsResponse | null): DependencyStatus | null {
  if (!detail) return null;
  return dep === "ffmpeg" ? detail.ffmpeg : detail.ytDlp;
}

/** Creates an independent install state machine for one dependency (e.g.
 * one for "ffmpeg", one for "ytDlp" -- each banner button gets its own, so
 * installing one never shows a spinner on the other). */
export function createInstallStore(dep: DepName) {
  const { subscribe, set, update } = writable<InstallState>(IDLE_STATE);

  async function install(): Promise<void> {
    if (!isTauri()) {
      // Browser (non-Tauri) fallback: no privileged install path exists
      // here, so this is a deliberate no-op -- the caller keeps today's
      // guidance-only copyable command instead.
      return;
    }

    set({ phase: "installing", outcome: null, outputTail: null, version: null });

    let result: InstallDependencyResult;
    try {
      const raw: unknown = await invoke("install_dependency", { name: dep });
      if (!isInstallDependencyResult(raw)) {
        throw new Error("install_dependency returned an unexpected response shape");
      }
      result = raw;
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      set({ phase: "failed", outcome: "failed", outputTail: message, version: null });
      return;
    }

    if (result.outcome !== "success") {
      set({ phase: "failed", outcome: result.outcome, outputTail: result.outputTail, version: null });
      return;
    }

    // The install command exited 0 -- now check whether it ACTUALLY took
    // effect, by re-running the same /v1/system/deps check the banner
    // already renders from. This is the "AFTER install, not just shown as
    // a command before" requirement.
    update((state) => ({ ...state, phase: "rechecking" }));
    await deps.recheck();

    const status = statusFor(dep, get(deps).detail);
    if (status?.found) {
      set({ phase: "ok", outcome: "success", outputTail: result.outputTail, version: status.version });
    } else {
      set({
        phase: "failed",
        outcome: "failed",
        outputTail:
          "The install finished, but this dependency still isn't detected. " +
          "If you just installed it, try restarting the app -- app windows already " +
          "running before an install can need a restart to see the new PATH entry.",
        version: null,
      });
    }
  }

  function reset(): void {
    set(IDLE_STATE);
  }

  return { subscribe, install, reset };
}
