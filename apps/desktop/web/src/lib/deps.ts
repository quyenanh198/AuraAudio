import { writable } from "svelte/store";

import { api } from "./api";
import type { SystemDepsResponse } from "./types";

export type DepsStatus = "checking" | "ok" | "missing";

export interface DepsState {
  status: DepsStatus;
  detail: SystemDepsResponse | null;
  error: string | null;
}

const initialState: DepsState = { status: "checking", detail: null, error: null };

export type Platform = "windows" | "macos" | "linux";

/** Per-OS one-line command to install ffmpeg (which also provides ffprobe). */
const INSTALL_COMMANDS: Record<Platform, string> = {
  windows: "winget install Gyan.FFmpeg",
  macos: "brew install ffmpeg",
  linux: "sudo apt install ffmpeg",
};

/** Minimal shape this needs from the real `Navigator`, so tests can pass a
 * plain object instead of stubbing the global. */
export interface PlatformHints {
  userAgent: string;
  platform: string;
}

export function detectPlatform(nav: PlatformHints = navigator): Platform {
  const source = `${nav.userAgent} ${nav.platform}`.toLowerCase();
  if (source.includes("win")) return "windows";
  if (source.includes("mac")) return "macos";
  return "linux";
}

export function installCommandFor(platformName: Platform): string {
  return INSTALL_COMMANDS[platformName];
}

function createDepsStore() {
  const { subscribe, set, update } = writable<DepsState>(initialState);

  async function check(): Promise<void> {
    // Preserve the previous `detail` across the "checking" transition (only
    // `status`/`error` reset) so a UI showing the missing-deps banner from a
    // prior check doesn't flash empty while a recheck() is in flight.
    update((state) => ({ ...state, status: "checking", error: null }));
    try {
      const detail = await api.getSystemDeps();
      set({ status: detail.allFound ? "ok" : "missing", detail, error: null });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      update((state) => ({ ...state, status: "missing", error: message }));
    }
  }

  return { subscribe, check, recheck: check };
}

export const deps = createDepsStore();
