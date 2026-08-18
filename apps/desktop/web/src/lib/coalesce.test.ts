import { describe, expect, it, vi } from "vitest";

import { createCoalescer } from "./coalesce";

/** A manually-driven scheduler: captures the flush callback instead of
 * invoking it, so tests can assert nothing ran yet and then trigger the
 * flush explicitly. */
function manualScheduler(): { schedule: (flush: () => void) => void; runPending: () => void } {
  let captured: (() => void) | null = null;
  return {
    schedule: (flush) => {
      captured = flush;
    },
    runPending: () => {
      const flush = captured;
      captured = null;
      flush?.();
    },
  };
}

describe("createCoalescer", () => {
  it("does not call apply until the scheduled flush runs", () => {
    const apply = vi.fn();
    const { schedule } = manualScheduler();
    const scheduleApply = createCoalescer(apply, schedule);

    scheduleApply(1);

    expect(apply).not.toHaveBeenCalled();
  });

  it("calls apply with the value once the flush runs", () => {
    const apply = vi.fn();
    const { schedule, runPending } = manualScheduler();
    const scheduleApply = createCoalescer(apply, schedule);

    scheduleApply(1);
    runPending();

    expect(apply).toHaveBeenCalledExactlyOnceWith(1);
  });

  it("collapses many calls before a flush into a single apply with the latest value", () => {
    const apply = vi.fn();
    const { schedule, runPending } = manualScheduler();
    const scheduleApply = createCoalescer(apply, schedule);

    scheduleApply(1);
    scheduleApply(2);
    scheduleApply(3);
    scheduleApply(4);
    runPending();

    expect(apply).toHaveBeenCalledExactlyOnceWith(4);
  });

  it("schedules the flush only once while a flush is already pending", () => {
    const apply = vi.fn();
    const scheduleFn = vi.fn();
    const scheduleApply = createCoalescer(apply, scheduleFn);

    scheduleApply(1);
    scheduleApply(2);
    scheduleApply(3);

    expect(scheduleFn).toHaveBeenCalledTimes(1);
  });

  it("schedules a fresh flush after a previous one completed", () => {
    const apply = vi.fn();
    const { schedule, runPending } = manualScheduler();
    const scheduleApply = createCoalescer(apply, schedule);

    scheduleApply(1);
    runPending();
    scheduleApply(2);
    runPending();

    expect(apply).toHaveBeenNthCalledWith(1, 1);
    expect(apply).toHaveBeenNthCalledWith(2, 2);
    expect(apply).toHaveBeenCalledTimes(2);
  });

  it("a flush with nothing newly pending (already consumed) is a no-op", () => {
    const apply = vi.fn();
    const { schedule, runPending } = manualScheduler();
    const scheduleApply = createCoalescer(apply, schedule);

    scheduleApply(1);
    runPending();
    apply.mockClear();

    // No scheduleApply() call happened since the last flush, so nothing was
    // (re-)scheduled — runPending() here has nothing captured to run.
    runPending();

    expect(apply).not.toHaveBeenCalled();
  });

  it("works with a real setTimeout-based scheduler under fake timers", () => {
    vi.useFakeTimers();
    try {
      const apply = vi.fn();
      const scheduleApply = createCoalescer<number>(apply, (flush) => setTimeout(flush, 0));

      scheduleApply(10);
      scheduleApply(20);
      scheduleApply(30);
      expect(apply).not.toHaveBeenCalled();

      vi.runAllTimers();

      expect(apply).toHaveBeenCalledExactlyOnceWith(30);
    } finally {
      vi.useRealTimers();
    }
  });
});
