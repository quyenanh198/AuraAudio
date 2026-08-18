import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { get } from "svelte/store";

import type { EditResponse, ScoreJson } from "./types";
import type { JobStatusResponse } from "./api";

const applyEditMock = vi.fn();
const undoEditMock = vi.fn();
const redoEditMock = vi.fn();
const revertEditsMock = vi.fn();
const getJobMock = vi.fn();

// A minimal EditApiError defined *inside* the mock factory (rather than
// importing the real api.ts class) so `new EditApiError(...)` in this file
// and the `instanceof EditApiError` check inside editor.ts both resolve
// through the same mocked module — see the note on vi.mock hoisting in
// task-5-report.md.
vi.mock("./api", () => {
  class EditApiError extends Error {
    readonly status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }
  return {
    EditApiError,
    api: {
      applyEdit: (...args: unknown[]) => applyEditMock(...args),
      undoEdit: (...args: unknown[]) => undoEditMock(...args),
      redoEdit: (...args: unknown[]) => redoEditMock(...args),
      revertEdits: (...args: unknown[]) => revertEditsMock(...args),
      getJob: (...args: unknown[]) => getJobMock(...args),
    },
  };
});

function score(overrides: Partial<ScoreJson> = {}): ScoreJson {
  return {
    schemaVersion: 4,
    timeMap: [
      { beat: 0, seconds: 0 },
      { beat: 1, seconds: 0.5 },
    ],
    parts: [],
    ...overrides,
  };
}

function editResponse(overrides: Partial<EditResponse> = {}): EditResponse {
  return { version: 1, score: score(), rederive_job_id: "job-1", ...overrides };
}

function job(status: string, overrides: Partial<JobStatusResponse> = {}): JobStatusResponse {
  return { id: "job-1", status, stage: null, progress: 0, error_code: null, error_detail: null, ...overrides };
}

describe("editor store", () => {
  beforeEach(() => {
    applyEditMock.mockReset();
    undoEditMock.mockReset();
    redoEditMock.mockReset();
    revertEditsMock.mockReset();
    getJobMock.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe("isTerminal", () => {
    it("is true only for succeeded/failed", async () => {
      const { isTerminal } = await import("./editor");
      expect(isTerminal("succeeded")).toBe(true);
      expect(isTerminal("failed")).toBe(true);
      expect(isTerminal("queued")).toBe(false);
      expect(isTerminal("running")).toBe(false);
    });
  });

  describe("select/clearSelection/setScore", () => {
    it("select() sets selectedEventId and clearSelection() nulls it", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();

      store.select("e1");
      expect(get(store).selectedEventId).toBe("e1");

      store.clearSelection();
      expect(get(store).selectedEventId).toBeNull();
    });

    it("setScore() replaces the score without touching other fields", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();
      store.select("e1");

      const s = score();
      store.setScore(s);

      const state = get(store);
      expect(state.score).toBe(s);
      expect(state.selectedEventId).toBe("e1");
    });
  });

  describe("apply()", () => {
    it("updates score immediately from the HTTP response, before the rederive job resolves", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();
      const applied = score();
      applyEditMock.mockResolvedValueOnce(editResponse({ score: applied }));
      getJobMock.mockImplementation(() => new Promise<JobStatusResponse>(() => {})); // never resolves

      await store.apply("p1", { type: "delete_note", eventId: "e1" });

      expect(applyEditMock).toHaveBeenCalledWith("p1", { type: "delete_note", eventId: "e1" });
      const state = get(store);
      expect(state.score).toBe(applied);
      expect(state.updating).toBe(true); // job hasn't resolved — still tracked
    });

    it("sets canUndo and clears canRedo on success", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();
      applyEditMock.mockResolvedValueOnce(editResponse());
      getJobMock.mockResolvedValue(job("succeeded"));

      await store.apply("p1", { type: "delete_note", eventId: "e1" });

      const state = get(store);
      expect(state.canUndo).toBe(true);
      expect(state.canRedo).toBe(false);
    });

    it("updating clears once the rederive job reaches a terminal status", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();
      const finalScore = score();
      applyEditMock.mockResolvedValueOnce(editResponse({ score: finalScore }));
      getJobMock
        .mockResolvedValueOnce(job("queued"))
        .mockResolvedValueOnce(job("running"))
        .mockResolvedValueOnce(job("succeeded"));

      await store.apply("p1", { type: "delete_note", eventId: "e1" });
      expect(get(store).updating).toBe(true);

      await vi.advanceTimersByTimeAsync(1500);

      const state = get(store);
      expect(state.updating).toBe(false);
      expect(state.score).toBe(finalScore);
      expect(state.error).toBeNull();
    });

    it("a failed rederive job sets rederiveError (from error_detail), NOT error, and clears updating", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();
      applyEditMock.mockResolvedValueOnce(editResponse());
      getJobMock.mockResolvedValueOnce(job("failed", { error_detail: "rederive blew up" }));

      await store.apply("p1", { type: "delete_note", eventId: "e1" });
      await vi.advanceTimersByTimeAsync(0);

      const state = get(store);
      expect(state.updating).toBe(false);
      expect(state.rederiveError).toBe("rederive blew up");
      expect(state.error).toBeNull();
    });

    it("a 422 sets error, NOT rederiveError, and leaves score unchanged", async () => {
      const { createEditorStore } = await import("./editor");
      const { EditApiError } = await import("./api");
      const store = createEditorStore();
      const before = score();
      store.setScore(before);

      applyEditMock.mockRejectedValueOnce(new EditApiError(422, "pitch must be an integer 0-127"));

      await store.apply("p1", { type: "set_pitch", eventId: "e1", pitch: 999 });

      const state = get(store);
      expect(state.error).toBe("pitch must be an integer 0-127");
      expect(state.rederiveError).toBeNull();
      expect(state.score).toBe(before);
      expect(state.updating).toBe(false);
      expect(getJobMock).not.toHaveBeenCalled();
    });

    it("starting a new apply() clears a stale rederiveError from a previous op's failed rederive", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();

      applyEditMock.mockResolvedValueOnce(editResponse());
      getJobMock.mockResolvedValueOnce(job("failed", { error_detail: "rederive blew up" }));
      await store.apply("p1", { type: "delete_note", eventId: "e1" });
      await vi.advanceTimersByTimeAsync(0);
      expect(get(store).rederiveError).toBe("rederive blew up");

      applyEditMock.mockResolvedValueOnce(editResponse());
      getJobMock.mockResolvedValue(job("succeeded"));
      await store.apply("p1", { type: "delete_note", eventId: "e2" });

      expect(get(store).rederiveError).toBeNull();
    });

    it("a stuck rederive job stops polling after MAX_POLL_ATTEMPTS and sets a timeout rederiveError", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();
      applyEditMock.mockResolvedValueOnce(editResponse());
      getJobMock.mockResolvedValue(job("running")); // never terminates

      await store.apply("p1", { type: "delete_note", eventId: "e1" });
      expect(get(store).updating).toBe(true);

      // 120 attempts * 500ms = 60s; add slack to be sure the cap has tripped.
      await vi.advanceTimersByTimeAsync(65_000);

      const state = get(store);
      expect(state.updating).toBe(false);
      expect(state.rederiveError).toBe("Notation update timed out — Retry");
      expect(state.error).toBeNull();

      // Polling actually stopped — no further getJob calls after the cap.
      const callsAtCap = getJobMock.mock.calls.length;
      await vi.advanceTimersByTimeAsync(5_000);
      expect(getJobMock.mock.calls.length).toBe(callsAtCap);
    });

    it("two rapid apply() calls serialize: the second's HTTP call waits for the first's, not its rederive poll", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();
      const order: string[] = [];

      let resolveFirst: (r: EditResponse) => void = () => {};
      const firstPending = new Promise<EditResponse>((resolve) => {
        resolveFirst = resolve;
      });
      applyEditMock.mockImplementationOnce(() => {
        order.push("apply-1-called");
        return firstPending;
      });
      applyEditMock.mockImplementationOnce(() => {
        order.push("apply-2-called");
        return Promise.resolve(editResponse());
      });
      getJobMock.mockImplementation(() => new Promise<JobStatusResponse>(() => {})); // never resolves

      const p1 = store.apply("p1", { type: "delete_note", eventId: "e1" });
      const p2 = store.apply("p1", { type: "delete_note", eventId: "e2" });

      // Let microtasks run — apply-2's HTTP call must not have fired while
      // apply-1's is still pending.
      await Promise.resolve();
      await Promise.resolve();
      expect(applyEditMock).toHaveBeenCalledTimes(1);
      expect(order).toEqual(["apply-1-called"]);

      resolveFirst(editResponse());
      await p1;
      await p2;

      expect(applyEditMock).toHaveBeenCalledTimes(2);
      expect(order).toEqual(["apply-1-called", "apply-2-called"]);
    });

    it("a stale rederive poll from an earlier apply() does not clobber a newer apply()'s state", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();
      const firstScore = score();
      const secondScore = score();

      applyEditMock
        .mockResolvedValueOnce(editResponse({ score: firstScore, rederive_job_id: "job-old" }))
        .mockResolvedValueOnce(editResponse({ score: secondScore, rederive_job_id: "job-new" }));

      let resolveOldJob: (j: JobStatusResponse) => void = () => {};
      const oldJobPending = new Promise<JobStatusResponse>((resolve) => {
        resolveOldJob = resolve;
      });
      getJobMock.mockImplementationOnce(() => oldJobPending);
      getJobMock.mockResolvedValueOnce(job("succeeded"));

      await store.apply("p1", { type: "delete_note", eventId: "e1" });
      await store.apply("p1", { type: "delete_note", eventId: "e2" });

      // The second (newer) apply's own fast poll should already have
      // cleared `updating`.
      await vi.advanceTimersByTimeAsync(0);
      expect(get(store).updating).toBe(false);
      expect(get(store).score).toBe(secondScore);

      // The first apply's stale poll now resolves as a FAILURE — it must
      // be ignored: its generation was superseded by the second apply().
      resolveOldJob(job("failed", { error_detail: "stale failure" }));
      await vi.advanceTimersByTimeAsync(0);

      const state = get(store);
      expect(state.error).toBeNull();
      expect(state.updating).toBe(false);
      expect(state.score).toBe(secondScore);
    });

    it("stop() invalidates an in-flight rederive poll so a late result can't write into the store", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();
      applyEditMock.mockResolvedValueOnce(editResponse({ rederive_job_id: "job-1" }));

      let resolveJob: (j: JobStatusResponse) => void = () => {};
      const jobPending = new Promise<JobStatusResponse>((resolve) => {
        resolveJob = resolve;
      });
      getJobMock.mockImplementationOnce(() => jobPending);

      await store.apply("p1", { type: "delete_note", eventId: "e1" });
      expect(get(store).updating).toBe(true);

      store.stop();

      resolveJob(job("failed", { error_detail: "too late" }));
      await vi.advanceTimersByTimeAsync(0);

      const state = get(store);
      // stop() only disowns the poll — it does not itself resolve
      // `updating`, since there is no longer any op to resolve it to a
      // known value. What matters is that the late failure never landed.
      expect(state.updating).toBe(true);
      expect(state.error).toBeNull();
    });
  });

  describe("undo()", () => {
    it("on success, updates score and sets both canUndo and canRedo", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();
      const undone = score();
      undoEditMock.mockResolvedValueOnce(editResponse({ score: undone }));
      getJobMock.mockResolvedValue(job("succeeded"));

      await store.undo("p1");

      const state = get(store);
      expect(state.score).toBe(undone);
      expect(state.canUndo).toBe(true);
      expect(state.canRedo).toBe(true);
    });

    it("409 flips canUndo false without setting a user-facing error", async () => {
      const { createEditorStore } = await import("./editor");
      const { EditApiError } = await import("./api");
      const store = createEditorStore();

      // Seed canUndo true, as a prior successful edit would have.
      applyEditMock.mockResolvedValueOnce(editResponse());
      getJobMock.mockResolvedValue(job("succeeded"));
      await store.apply("p1", { type: "delete_note", eventId: "e1" });
      expect(get(store).canUndo).toBe(true);

      undoEditMock.mockRejectedValueOnce(new EditApiError(409, "nothing to undo"));
      await store.undo("p1");

      const state = get(store);
      expect(state.canUndo).toBe(false);
      expect(state.error).toBeNull();
      expect(state.updating).toBe(false);
    });
  });

  describe("redo()", () => {
    it("on success, updates score and sets both canUndo and canRedo", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();
      const redone = score();
      redoEditMock.mockResolvedValueOnce(editResponse({ score: redone }));
      getJobMock.mockResolvedValue(job("succeeded"));

      await store.redo("p1");

      const state = get(store);
      expect(state.score).toBe(redone);
      expect(state.canUndo).toBe(true);
      expect(state.canRedo).toBe(true);
    });

    it("409 flips canRedo false without setting a user-facing error", async () => {
      const { createEditorStore } = await import("./editor");
      const { EditApiError } = await import("./api");
      const store = createEditorStore();

      redoEditMock.mockRejectedValueOnce(new EditApiError(409, "nothing to redo"));
      await store.redo("p1");

      const state = get(store);
      expect(state.canRedo).toBe(false);
      expect(state.error).toBeNull();
      expect(state.updating).toBe(false);
    });
  });

  describe("revert()", () => {
    it("on success, updates score, clears canUndo, and sets canRedo", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();
      const reverted = score();
      revertEditsMock.mockResolvedValueOnce(editResponse({ score: reverted }));
      getJobMock.mockResolvedValue(job("succeeded"));

      await store.revert("p1");

      const state = get(store);
      expect(state.score).toBe(reverted);
      expect(state.canUndo).toBe(false);
      expect(state.canRedo).toBe(true);
    });

    it("409 ('no edits to revert') is swallowed: no error, no score change", async () => {
      const { createEditorStore } = await import("./editor");
      const { EditApiError } = await import("./api");
      const store = createEditorStore();
      const before = score();
      store.setScore(before);

      revertEditsMock.mockRejectedValueOnce(new EditApiError(409, "no edits to revert"));
      await store.revert("p1");

      const state = get(store);
      expect(state.error).toBeNull();
      expect(state.score).toBe(before);
      expect(state.updating).toBe(false);
    });
  });

  describe("reset()", () => {
    it("clears score/selectedEventId/updating/error/rederiveError, and resets canUndo/canRedo to their optimistic true default", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();

      // Build up state a real editing session would leave behind: a
      // selection, and (via a successful apply()) canUndo/score.
      store.select("e1");
      applyEditMock.mockResolvedValueOnce(editResponse());
      getJobMock.mockResolvedValue(job("succeeded"));
      await store.apply("p1", { type: "delete_note", eventId: "e1" });
      expect(get(store).canUndo).toBe(true);
      expect(get(store).selectedEventId).toBe("e1");
      expect(get(store).score).not.toBeNull();

      store.reset();

      const state = get(store);
      expect(state.selectedEventId).toBeNull();
      expect(state.score).toBeNull();
      expect(state.updating).toBe(false);
      // Optimistic default (IMPORTANT 3) — NOT false: a reopened project has
      // no way to know its real server-side history bounds up front, and the
      // History buttons must not go dead just because the store doesn't know
      // yet. The first undo/redo press discovers the real bound via a 409.
      expect(state.canUndo).toBe(true);
      expect(state.canRedo).toBe(true);
      expect(state.error).toBeNull();
      expect(state.rederiveError).toBeNull();
    });

    it("abandons a mid-flight rederive poll: a late resolution after reset() never writes into the store, and a subsequent apply() still works", async () => {
      const { createEditorStore } = await import("./editor");
      const store = createEditorStore();

      applyEditMock.mockResolvedValueOnce(editResponse({ rederive_job_id: "job-stale" }));
      let resolveStaleJob: (j: JobStatusResponse) => void = () => {};
      const staleJobPending = new Promise<JobStatusResponse>((resolve) => {
        resolveStaleJob = resolve;
      });
      getJobMock.mockImplementationOnce(() => staleJobPending);

      await store.apply("p1", { type: "delete_note", eventId: "e1" });
      expect(get(store).updating).toBe(true); // poll still in flight

      store.reset();
      expect(get(store)).toEqual({
        selectedEventId: null,
        score: null,
        updating: false,
        canUndo: true,
        canRedo: true,
        error: null,
        rederiveError: null,
      });

      // The abandoned poll's late result must not resurrect any state.
      resolveStaleJob(job("failed", { error_detail: "stale, post-reset failure" }));
      await vi.advanceTimersByTimeAsync(0);
      expect(get(store)).toEqual({
        selectedEventId: null,
        score: null,
        updating: false,
        canUndo: true,
        canRedo: true,
        error: null,
        rederiveError: null,
      });

      // A subsequent apply() on the freshly-reset store still works
      // end-to-end (new project's first edit).
      const freshScore = score();
      applyEditMock.mockResolvedValueOnce(editResponse({ score: freshScore, rederive_job_id: "job-fresh" }));
      getJobMock.mockResolvedValueOnce(job("succeeded"));

      await store.apply("p2", { type: "delete_note", eventId: "e2" });

      const state = get(store);
      expect(state.score).toBe(freshScore);
      expect(state.canUndo).toBe(true);
      expect(state.canRedo).toBe(false);
      expect(state.error).toBeNull();
    });
  });
});
