// A small "latest-wins" scheduling coalescer. Many calls to the returned
// function in quick succession collapse into at most one `apply(value)`
// call per scheduled flush, always using the most recently passed value —
// intermediate values are dropped.
//
// Generic over `schedule` so it's fully unit-testable without a real
// `requestAnimationFrame`/`setTimeout` (inject a fake scheduler, or drive it
// with vitest's fake timers via a `setTimeout`-based one). Production
// callers pass a one-line `(flush) => requestAnimationFrame(flush)` — that
// wrapper itself is DOM-bound and not unit tested, but everything it wraps
// (this function) is.

export type Scheduler = (flush: () => void) => void;

export function createCoalescer<T>(apply: (value: T) => void, schedule: Scheduler): (value: T) => void {
  let pendingValue: T | undefined;
  let hasPending = false;
  let flushScheduled = false;

  function flush(): void {
    flushScheduled = false;
    if (!hasPending) return;
    hasPending = false;
    const value = pendingValue as T;
    pendingValue = undefined;
    apply(value);
  }

  return function scheduleApply(value: T): void {
    pendingValue = value;
    hasPending = true;
    if (flushScheduled) return;
    flushScheduled = true;
    schedule(flush);
  };
}
