import { writable } from "svelte/store";

import { api } from "./api";
import type { SystemDepsResponse } from "./types";

// "missing" means the check succeeded and reported at least one binary
// absent — the install-guidance banner applies. "error" means the check
// itself failed (network error, backend not up yet, non-2xx, etc.) — the
// real dependency state is unknown, so a distinct "couldn't check" banner
// applies instead of misleadingly telling the user ffmpeg isn't installed.
export type DepsStatus = "checking" | "ok" | "missing" | "error";

export interface DepsState {
  status: DepsStatus;
  detail: SystemDepsResponse | null;
  error: string | null;
}

const initialState: DepsState = { status: "checking", detail: null, error: null };

export type Platform = "windows" | "macos" | "linux";

/** The PATH dependencies this app has guided-install banners for. Extend
 * this (and `INSTALL_COMMANDS` below) rather than adding a new parallel
 * per-OS command map when a future dependency needs the same treatment. */
export type DepName = "ffmpeg" | "ytDlp";

/** Per-OS one-line install command, per dependency. ffmpeg also provides
 * ffprobe, so one command covers both required binaries. */
const INSTALL_COMMANDS: Record<DepName, Record<Platform, string>> = {
  ffmpeg: {
    windows: "winget install Gyan.FFmpeg",
    macos: "brew install ffmpeg",
    linux: "sudo apt install ffmpeg",
  },
  ytDlp: {
    windows: "winget install yt-dlp",
    macos: "brew install yt-dlp",
    linux: "sudo apt install yt-dlp",
  },
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

export function installCommandFor(platformName: Platform, dep: DepName = "ffmpeg"): string {
  return INSTALL_COMMANDS[dep][platformName];
}

/** True only once a check has actually completed and reported yt-dlp
 * absent -- mirrors the ffmpeg banner's "only gate on a CONFIRMED missing
 * binary" rule in Home.svelte (never gate on `detail === null`, i.e. still
 * checking or the check itself failed; that's not proof yt-dlp is
 * missing). yt-dlp is optional, so this is intentionally independent of
 * `state.status`, which only reflects the required ffmpeg/ffprobe deps. */
export function isYtDlpMissing(state: Pick<DepsState, "detail">): boolean {
  return state.detail !== null && !state.detail.ytDlp.found;
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
      update((state) => ({ ...state, status: "error", error: message }));
    }
  }

  return { subscribe, check, recheck: check };
}

export const deps = createDepsStore();
