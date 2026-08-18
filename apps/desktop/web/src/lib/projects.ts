import { writable } from "svelte/store";

import { api } from "./api";
import { TERMINAL_JOB_STATUSES, type ProjectListItem } from "./types";

const POLL_INTERVAL_MS = 1000;

export interface ProjectsState {
  items: ProjectListItem[];
  loading: boolean;
  error: string | null;
}

const initialState: ProjectsState = { items: [], loading: false, error: null };

/** Pure predicate: true while any project's latest job hasn't reached a
 * terminal state (`succeeded` | `failed`). Extracted standalone so it's
 * unit-testable without touching the store or the network. */
export function hasActiveJob(items: ProjectListItem[]): boolean {
  return items.some((item) => item.job !== null && !TERMINAL_JOB_STATUSES.has(item.job.status));
}

function createProjectsStore() {
  const { subscribe, set, update } = writable<ProjectsState>(initialState);
  let pollHandle: ReturnType<typeof setInterval> | null = null;
  // Bumped every time stopPolling() runs. A refresh() in flight when that
  // happens (the initial onMount fetch, or the tail of an interval tick)
  // captures the generation it started with; if stopPolling() advances the
  // counter before that fetch resolves, the resolved call must not be
  // allowed to schedule a new interval — there's no live component left to
  // stop it again. See task-5-report.md's fix-report section for the race
  // this closes.
  let generation = 0;

  function stopPolling(): void {
    generation += 1;
    if (pollHandle !== null) {
      clearInterval(pollHandle);
      pollHandle = null;
    }
  }

  async function refresh(): Promise<void> {
    const startedAtGeneration = generation;
    update((state) => ({ ...state, loading: true, error: null }));
    try {
      const items = await api.listProjects();
      set({ items, loading: false, error: null });
      if (startedAtGeneration !== generation) {
        // stopPolling() ran while this fetch was in flight — the fetched
        // data is still committed above (it's not stale, just late), but
        // scheduling a new interval here would resurrect polling with
        // nothing left to stop it.
        return;
      }
      if (hasActiveJob(items)) {
        if (pollHandle === null) {
          pollHandle = setInterval(() => {
            void refresh();
          }, POLL_INTERVAL_MS);
        }
      } else {
        stopPolling();
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      update((state) => ({ ...state, loading: false, error: message }));
    }
  }

  return { subscribe, refresh, stopPolling };
}

export const projects = createProjectsStore();
