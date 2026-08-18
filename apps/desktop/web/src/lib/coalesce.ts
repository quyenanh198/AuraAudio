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

/** The function `createCoalescer` returns: callable exactly like before
 * (`scheduleApply(value)`), plus a `cancel()` for discarding a pending
 * value before it's ever applied. */
export interface Coalescer<T> {
  (value: T): void;
  /** Discards any pending value so it is never passed to `apply` — call
   * this on unmount/teardown, before tearing down whatever `apply` reads
   * or touches (e.g. a disposed OSMD cursor). Note this cannot reach into
   * a browser-level `requestAnimationFrame`/`setTimeout` id to cancel the
   * OS-level callback itself (the `Scheduler` type never hands one back);
   * if the underlying scheduled callback still fires later it finds
   * nothing pending and is a no-op, same as any other already-flushed
   * cycle. Safe to call whether or not a flush is currently pending. */
  cancel(): void;
}

export function createCoalescer<T>(apply: (value: T) => void, schedule: Scheduler): Coalescer<T> {
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

  const scheduleApply = ((value: T): void => {
    pendingValue = value;
    hasPending = true;
    if (flushScheduled) return;
    flushScheduled = true;
    schedule(flush);
  }) as Coalescer<T>;

  scheduleApply.cancel = (): void => {
    hasPending = false;
    pendingValue = undefined;
  };

  return scheduleApply;
}
