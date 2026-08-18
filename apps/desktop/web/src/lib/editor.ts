// Editor store contract: {selectedEventId, score, updating, canUndo,
// canRedo, error, rederiveError} with select(id), clearSelection(),
// setScore(score), apply(projectId, op), undo(projectId), redo(projectId),
// revert(projectId), stop() for polling teardown, and reset() for a full
// cross-project state reset (see reset()'s own doc comment below).
//
// `error` vs `rederiveError`: these are deliberately two separate fields,
// not one shared "something went wrong" string. `error` is set ONLY when
// the op itself is rejected (an apply/undo/redo/revert HTTP call fails,
// most commonly a 422 from apply()'s op validation) — it's consumed inline,
// per-control, by Sidebar's `fieldError()`. `rederiveError` is set ONLY
// from pollJob's failure path (the async rederive job itself failing after
// the op already succeeded) — it's consumed by ScoreView's dismissible
// banner + Retry. Conflating them into one `error` field made a rejected
// edit (422) surface as a whole-view "rederive failed" banner whose Retry
// (re-applying set_locked) implied the rejected edit had actually gone
// through. Every mutating call's start clears BOTH — a fresh op means any
// stale banner from a previous op's rederive is no longer relevant.
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
//
// initialState/reset() start canUndo/canRedo at TRUE, not false — a
// deliberate semantic change from an earlier version of this store that
// started both false. This store has no way to learn a reopened project's
// real server-side history bounds (there's no "GET history state" call),
// so a project that already had edits before the app was closed always
// reopened with dead History buttons, even though the same undo was one
// Ctrl+Z away (the keyboard shortcut calls undo()/redo() directly, bypassing
// the button's `disabled` gate entirely). Starting optimistic and letting
// the FIRST press discover the real bound via undo_edit/redo_edit's 409 (see
// above) is the cheapest fix that keeps the buttons truthful once any
// history call has actually been made — a stale-true button costs one wasted
// click that 409s harmlessly and flips itself off; a stale-false button
// costs the feature outright.

import { writable } from "svelte/store";

import { api, EditApiError } from "./api";
import { TERMINAL_JOB_STATUSES, type EditOp, type EditResponse, type ScoreJson } from "./types";
import type { JobStatusResponse } from "./api";

const POLL_INTERVAL_MS = 500;
// Cap on rederive-job poll attempts (~120 * 500ms = 60s) — a job stuck in
// "queued"/"running" forever (worker crash, dropped message, ...) would
// otherwise re-arm setTimeout indefinitely, leaving `updating` (and the
// "Updating notation…" pill it drives) pinned on with no way for the user
// to recover short of reloading the page.
const MAX_POLL_ATTEMPTS = 120;

export interface EditorState {
  selectedEventId: string | null;
  score: ScoreJson | null;
  updating: boolean;
  canUndo: boolean;
  canRedo: boolean;
  error: string | null;
  rederiveError: string | null;
}

const initialState: EditorState = {
  selectedEventId: null,
  score: null,
  updating: false,
  // Optimistic default — see the module-level comment above on why this
  // isn't false.
  canUndo: true,
  canRedo: true,
  error: null,
  rederiveError: null,
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
    let attempts = 0;
    const tick = async (): Promise<void> => {
      if (myGeneration !== generation) return;
      let job: JobStatusResponse;
      try {
        job = await api.getJob(jobId);
      } catch (err: unknown) {
        if (myGeneration !== generation) return;
        update((s) => ({ ...s, updating: false, rederiveError: errorMessage(err) }));
        return;
      }
      if (myGeneration !== generation) return;
      if (!isTerminal(job.status)) {
        attempts += 1;
        if (attempts >= MAX_POLL_ATTEMPTS) {
          update((s) => ({
            ...s,
            updating: false,
            rederiveError: "Notation update timed out — Retry",
          }));
          return;
        }
        setTimeout(() => void tick(), POLL_INTERVAL_MS);
        return;
      }
      if (job.status === "failed") {
        update((s) => ({
          ...s,
          updating: false,
          rederiveError: job.error_detail ?? job.error_code ?? "rederive failed",
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
      update((s) => ({ ...s, updating: true, error: null, rederiveError: null }));

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

  /** Resets the store to its initial state (score/selectedEventId/updating/
   * error/rederiveError cleared, canUndo/canRedo back to their optimistic
   * `true` default — see the module-level comment above) and invalidates any
   * in-flight rederive poll — call when a fresh `ScoreView` mounts for a
   * DIFFERENT project. `editor` is a module-level singleton and the hash
   * router swaps `ScoreView` instances without a full page reload, so
   * without this a project switch leaks the outgoing project's
   * `updating`/`canUndo`/`canRedo`/`error`/`selectedEventId` into the
   * incoming project's freshly-loaded view until its own first edit
   * happens to overwrite them (see task-7-report.md's "editor store has no
   * cross-project reset" finding, closed by this method).
   *
   * Bumps `generation` itself rather than relying on a prior `stop()` call
   * (e.g. from the outgoing view's `onDestroy`) — a poll still in flight
   * at the moment `reset()` runs is abandoned exactly like `stop()`'s,
   * regardless of call order. */
  function reset(): void {
    generation += 1;
    update(() => initialState);
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
    reset,
  };
}

export const editor = createEditorStore();
