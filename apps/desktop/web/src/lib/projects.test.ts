import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { get } from "svelte/store";

import type { ProjectListItem } from "./types";

const listProjectsMock = vi.fn();

vi.mock("./api", () => ({
  api: {
    listProjects: (...args: unknown[]) => listProjectsMock(...args),
  },
}));

function job(status: string): NonNullable<ProjectListItem["job"]> {
  return { id: "job-1", status, stage: null, progress: 0 };
}

function project(overrides: Partial<ProjectListItem> = {}): ProjectListItem {
  return {
    id: "p1",
    title: "Test",
    instrument: "guitar",
    created_at: new Date().toISOString(),
    duration_ms: 1000,
    job: job("running"),
    exports: [],
    ...overrides,
  };
}

describe("hasActiveJob", () => {
  it("returns false for an empty list", async () => {
    const { hasActiveJob } = await import("./projects");
    expect(hasActiveJob([])).toBe(false);
  });

  it("returns false when every job is terminal (succeeded or failed)", async () => {
    const { hasActiveJob } = await import("./projects");
    expect(
      hasActiveJob([project({ job: job("succeeded") }), project({ id: "p2", job: job("failed") })]),
    ).toBe(false);
  });

  it("returns true when any job is non-terminal", async () => {
    const { hasActiveJob } = await import("./projects");
    expect(
      hasActiveJob([project({ job: job("succeeded") }), project({ id: "p2", job: job("queued") })]),
    ).toBe(true);
  });

  it("treats a project with no job at all as inactive", async () => {
    const { hasActiveJob } = await import("./projects");
    expect(hasActiveJob([project({ job: null })])).toBe(false);
  });
});

describe("projects store polling", () => {
  beforeEach(() => {
    vi.resetModules();
    listProjectsMock.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("polls every second while a job is active and stops once all jobs are terminal", async () => {
    listProjectsMock
      .mockResolvedValueOnce([project({ job: job("running") })])
      .mockResolvedValueOnce([project({ job: job("running") })])
      .mockResolvedValueOnce([project({ job: job("succeeded") })]);

    const { projects } = await import("./projects");

    await projects.refresh();
    expect(listProjectsMock).toHaveBeenCalledTimes(1);
    expect(get(projects).items[0].job?.status).toBe("running");

    await vi.advanceTimersByTimeAsync(1000);
    expect(listProjectsMock).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(1000);
    expect(listProjectsMock).toHaveBeenCalledTimes(3);
    expect(get(projects).items[0].job?.status).toBe("succeeded");

    // The job just went terminal — the interval must have been cleared, so
    // further elapsed time triggers no more fetches.
    await vi.advanceTimersByTimeAsync(5000);
    expect(listProjectsMock).toHaveBeenCalledTimes(3);
  });

  it("never starts polling when the initial fetch already has no active jobs", async () => {
    listProjectsMock.mockResolvedValue([project({ job: job("succeeded") })]);

    const { projects } = await import("./projects");
    await projects.refresh();
    expect(listProjectsMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(5000);
    expect(listProjectsMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces a fetch error on the store instead of throwing", async () => {
    listProjectsMock.mockRejectedValueOnce(new Error("boom"));

    const { projects } = await import("./projects");
    await projects.refresh();

    const state = get(projects);
    expect(state.error).toBe("boom");
    expect(state.loading).toBe(false);
  });

  it("stopPolling halts an in-progress interval immediately", async () => {
    listProjectsMock.mockResolvedValue([project({ job: job("running") })]);

    const { projects } = await import("./projects");
    await projects.refresh();
    expect(listProjectsMock).toHaveBeenCalledTimes(1);

    projects.stopPolling();
    await vi.advanceTimersByTimeAsync(5000);
    expect(listProjectsMock).toHaveBeenCalledTimes(1);
  });

  it("does not resurrect polling when stopPolling() runs while a refresh() is still in flight", async () => {
    // Pin the race from the review: onDestroy -> stopPolling() can land
    // while the onMount (or in-flight interval tick) refresh() is still
    // awaiting the network. That refresh() must not be allowed to schedule
    // a fresh interval once it finally resolves — there's no component
    // left alive to stop it.
    let resolveFetch: (items: ReturnType<typeof project>[]) => void = () => {};
    const pending = new Promise<ReturnType<typeof project>[]>((resolve) => {
      resolveFetch = resolve;
    });
    listProjectsMock.mockReturnValueOnce(pending);

    const { projects } = await import("./projects");

    const refreshPromise = projects.refresh();
    // Simulate the owning component unmounting mid-fetch.
    projects.stopPolling();

    resolveFetch([project({ job: job("running") })]);
    await refreshPromise;

    expect(listProjectsMock).toHaveBeenCalledTimes(1);
    // The late data is still committed to the store...
    expect(get(projects).items[0].job?.status).toBe("running");

    // ...but no interval should have been (re)armed from the stale fetch,
    // even though its result reports an active job.
    await vi.advanceTimersByTimeAsync(5000);
    expect(listProjectsMock).toHaveBeenCalledTimes(1);
  });
});
