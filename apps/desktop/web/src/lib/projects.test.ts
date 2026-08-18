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
});
