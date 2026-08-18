// Editor store contract: {selectedEventId, score, updating, canUndo,
// canRedo, error} with select(id), clearSelection(), setScore(score),
// apply(projectId, op), undo(projectId), redo(projectId), revert(projectId),
// and stop() for polling teardown.
//
// Deviation from the plan's abbreviated `apply(op)`/`undo()`/`redo()`/
// `revert()` signatures (documented here since T6/T7 consume these exact
// names): every one of those calls a `/v1/projects/{id}/edits...` endpoint
// (see api.ts), and unlike `projects`/`playback` this store has no other
// source for "which project" — there's no prior `setProjectId`-style call
// in the plan to hang that on. So each mutating method takes `projectId` as
// its first argument, mirroring the api.ts methods it wraps 1:1.
//
// canUndo/canRedo are optimistic, not authoritative: apply() sets
// {canUndo: true, canRedo: false} because a successful edit always makes
// undo available and always cuts the redo branch server-side (apps/api/...
// /edits.py::apply_project_edit deletes every revision past the new head).
// undo()/redo() set both flags true (assume more history in that direction
// until told otherwise); the real bound is discovered lazily, the same way
// a disabled-button-that-isn't would be discovered: undo_edit/redo_edit
// return 409 exactly when the bound is hit (edits.py's `head.parent_id is
// None or head.created_by == "baseline"` / "no child revision" checks), and
// that 409 is what flips the corresponding flag back to false. revert()'s
// 409 ("no edits to revert") has no matching UI flag in this state shape,
// so it's swallowed the same way a benign bound is elsewhere: updating
// clears, nothing else changes, no `error` is set.

import { writable } from "svelte/store";

import { api, EditApiError } from "./api";
import { TERMINAL_JOB_STATUSES, type EditOp, type EditResponse, type ScoreJson } from "./types";
import type { JobStatusResponse } from "./api";

const POLL_INTERVAL_MS = 500;

export interface EditorState {
  selectedEventId: string | null;
  score: ScoreJson | null;
  updating: boolean;
  canUndo: boolean;
  canRedo: boolean;
  error: string | null;
}

const initialState: EditorState = {
  selectedEventId: null,
  score: null,
  updating: false,
  canUndo: false,
  canRedo: false,
  error: null,
};

/** Pure predicate, exported for tests: true once a rederive job has reached
 * a terminal status. Anything else (queued, running, ...) is still in
 * flight — same convention as `TERMINAL_JOB_STATUSES` in types.ts. */
export function isTerminal(status: string): boolean {
  return TERMINAL_JOB_STATUSES.has(status);
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function createEditorStore() {
  const { subscribe, update } = writable<EditorState>(initialState);

  // Bumped at the start of every apply()/undo()/redo()/revert() call, and by
  // stop(). A rederive-job poll loop captures the generation it started
  // under; if the generation has moved on by the time a tick runs — a newer
  // op superseded it, or stop() tore the store down — that tick abandons
  // silently instead of writing `updating`/`error` for an op that is no
  // longer the current one. Mirrors projects.ts's generation guard, for the
  // identical reason: a stale async result must not resurrect state nobody
  // owns anymore.
  let generation = 0;

  // FIFO queue: two rapid apply()/undo()/redo()/revert() calls must issue
  // their HTTP requests in call order — network responses are not
  // guaranteed to resolve in the order the requests were sent, and each op
  // is only meaningful applied against the previous op's *result*. Queuing
  // means the second call's `api.*Edit()` isn't even invoked until the
  // first call's HTTP round-trip (and immediate score/flag update) has
  // settled. Each call's own rederive-job polling is explicitly NOT part of
  // what's queued — it's started (via pollJob) and left running in the
  // background, so a slow rederive never blocks the next edit from being
  // sent.
  let queue: Promise<void> = Promise.resolve();

  function enqueue(fn: () => Promise<void>): Promise<void> {
    const result = queue.then(fn);
    // Swallow so a failed op (already handled internally — see runOp) can
    // never wedge the queue for every op that follows it.
    queue = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  function pollJob(jobId: string, myGeneration: number): void {
    const tick = async (): Promise<void> => {
      if (myGeneration !== generation) return;
      let job: JobStatusResponse;
      try {
        job = await api.getJob(jobId);
      } catch (err: unknown) {
        if (myGeneration !== generation) return;
        update((s) => ({ ...s, updating: false, error: errorMessage(err) }));
        return;
      }
      if (myGeneration !== generation) return;
      if (!isTerminal(job.status)) {
        setTimeout(() => void tick(), POLL_INTERVAL_MS);
        return;
      }
      if (job.status === "failed") {
        update((s) => ({
          ...s,
          updating: false,
          error: job.error_detail ?? job.error_code ?? "rederive failed",
        }));
        return;
      }
      update((s) => ({ ...s, updating: false }));
    };
    void tick();
  }

  /** Shared body for apply/undo/redo/revert: queues the call, marks
   * `updating` for its duration, applies `onSuccess`'s state patch and
   * kicks off rederive polling on success, and on failure either applies
   * `onBounds`'s patch (409, when the caller supplied one — an expected
   * bound, not a user-facing error) or sets `error` (anything else). */
  function runOp(
    call: () => Promise<EditResponse>,
    onSuccess: (response: EditResponse) => Partial<EditorState>,
    onBounds?: () => Partial<EditorState>,
  ): Promise<void> {
    return enqueue(async () => {
      generation += 1;
      const myGeneration = generation;
      update((s) => ({ ...s, updating: true, error: null }));

      let response: EditResponse;
      try {
        response = await call();
      } catch (err: unknown) {
        if (onBounds && err instanceof EditApiError && err.status === 409) {
          update((s) => ({ ...s, ...onBounds(), updating: false }));
          return;
        }
        update((s) => ({ ...s, updating: false, error: errorMessage(err) }));
        return;
      }

      update((s) => ({ ...s, ...onSuccess(response) }));
      pollJob(response.rederive_job_id, myGeneration);
    });
  }

  function select(id: string): void {
    update((s) => ({ ...s, selectedEventId: id }));
  }

  function clearSelection(): void {
    update((s) => ({ ...s, selectedEventId: null }));
  }

  function setScore(score: ScoreJson): void {
    update((s) => ({ ...s, score }));
  }

  function apply(projectId: string, op: EditOp): Promise<void> {
    return runOp(
      () => api.applyEdit(projectId, op),
      (response) => ({ score: response.score, canUndo: true, canRedo: false }),
    );
  }

  function undo(projectId: string): Promise<void> {
    return runOp(
      () => api.undoEdit(projectId),
      (response) => ({ score: response.score, canUndo: true, canRedo: true }),
      () => ({ canUndo: false }),
    );
  }

  function redo(projectId: string): Promise<void> {
    return runOp(
      () => api.redoEdit(projectId),
      (response) => ({ score: response.score, canRedo: true, canUndo: true }),
      () => ({ canRedo: false }),
    );
  }

  function revert(projectId: string): Promise<void> {
    return runOp(
      () => api.revertEdits(projectId),
      (response) => ({ score: response.score, canUndo: false, canRedo: true }),
      () => ({}),
    );
  }

  /** Invalidates any in-flight rederive poll without starting new work —
   * call on component teardown (or before navigating to a different
   * project) so a late-resolving job from the outgoing project can never
   * write `updating`/`error` into a store that's now representing a
   * different one. */
  function stop(): void {
    generation += 1;
  }

  return {
    subscribe,
    select,
    clearSelection,
    setScore,
    apply,
    undo,
    redo,
    revert,
    stop,
  };
}

export const editor = createEditorStore();
