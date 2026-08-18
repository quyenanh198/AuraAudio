<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import type { Note } from "opensheetmusicdisplay";

  import { api } from "../lib/api";
  import { createCoalescer } from "../lib/coalesce";
  import { buildEventPositionIndex, type StepNoteInfo } from "../lib/correlate";
  import { editor } from "../lib/editor";
  import { clampPitch, findEvent, firstEventId, stepOnset } from "../lib/noteEdit";
  import { createAudioSource, playback } from "../lib/playback";
  import { createSynthSource, type SynthInstrument, type SynthPlaybackSource } from "../lib/synth";
  import { buildTimeline, cursorIndexAt, desiredNextCallsFor, planCursorMove, type TimelineEntry } from "../lib/timeline";
  import type { ProjectListItem, ScoreJson, ScorePart } from "../lib/types";
  import Notation, { type OSMDCursorHandle } from "./Notation.svelte";
  import Sidebar from "./Sidebar.svelte";
  import Transport from "./Transport.svelte";

  interface Props {
    projectId: string;
  }

  let { projectId }: Props = $props();

  const MIN_ZOOM_PERCENT = 50;
  const MAX_ZOOM_PERCENT = 200;
  const ZOOM_STEP_PERCENT = 10;
  // How often the rAF loop is allowed to re-issue a smooth scrollIntoView()
  // on the cursor element — every step change would otherwise fight itself
  // (a new smooth-scroll starting before the last one settles) on fast runs
  // of short notes.
  const SCROLL_THROTTLE_MS = 200;

  // Explicit generic (not just an LHS annotation) on the nullable $state()
  // calls — with `$state(null)` alone, svelte-check (svelte-check@4.7.3 /
  // svelte@5.56 / typescript@6.0.2, this project's pinned versions) infers
  // the reactive type as the literal `null` and silently ignores the `let`
  // annotation, which only surfaces as "Property 'x' does not exist on type
  // 'never'" wherever the value is narrowed or accessed. Confirmed by a
  // minimal repro before writing this comment.
  let project = $state<ProjectListItem | null>(null);
  let score = $state<ScoreJson | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  /** CRITICAL 1(b): set when the score itself loaded and rendered fine but
   * building the playback timeline / walking the OSMD cursor failed (e.g.
   * `buildTimeline`'s count-mismatch guard) — kept SEPARATE from `error`
   * on purpose. `error` blanks the whole view (nothing usable rendered);
   * this instead leaves the rendered score up and only disables playback
   * sync, surfaced as a small inline, non-blocking notice. */
  let syncError = $state<string | null>(null);

  let sidebarCollapsed = $state(false);
  let zoomPercent = $state(100);
  let tabVisible = $state(true);
  /** Whether the user dismissed the current rederive-failure banner (Task 7
   * Step 4). Reset to `false` whenever a NEW `editor.error` value arrives —
   * see the refresh-loop `$effect` below — so a fresh failure always shows,
   * even if an earlier one was dismissed. */
  let rederiveErrorDismissed = $state(false);

  let notation: Notation | undefined = $state();
  let audioEl: HTMLAudioElement | undefined = $state();

  // Cursor-sync state — not $state: these are imperative bookkeeping for the
  // rAF loop and the seek handler, not reactive UI values. Rebuilt every
  // loadScore() (see there for the reset).
  let cursorHandle: OSMDCursorHandle | null = null;
  let timeline: TimelineEntry[] = [];
  /** The synth `PlaybackSource` for the currently loaded project — owns a
   * real `AudioContext`, so it's explicitly disposed (not just garbage
   * collected) whenever `loadScore()` replaces it or the component
   * unmounts. Rebuilt every `loadScore()` since it's derived from the
   * score's events + instrument. */
  let synthSource: SynthPlaybackSource | null = null;
  /** Index into `timeline` the cursor is currently showing; -1 = "before the
   * first entry" (freshly reset, nothing has sounded yet). */
  let lastTimelineIndex = -1;
  /** next() calls performed since the OSMD cursor's last reset() — see
   * timeline.ts's planCursorMove() for why this (not the OSMD step index
   * itself) is what the move-planner needs. */
  let performedNextCalls = 0;
  let rafId: number | null = null;
  let lastScrollAt = 0;

  // --- Task 7: refresh loop / synth-follows-store bookkeeping ------------
  //
  // All plain (non-`$state`) bookkeeping, same convention as the cursor-sync
  // fields above: these back reactive `$effect`s that need to compare
  // "what changed since last time", not values the template reads directly.
  /** The `ScoreJson` reference `synthSource` was last built from — guards
   * the synth-rebuild effect below against rebuilding twice for the same
   * `editor.score` write (once from `loadScore()`'s own explicit synth
   * creation, once from the effect noticing the same store write) and
   * against rebuilding for a reference-identical no-op update. */
  let lastSynthScore: ScoreJson | null = null;
  /** `editor.updating`'s value as of the last time the refresh-loop effect
   * ran — a true->false transition (with no `editor.error`) is what starts
   * `refreshAfterEdit()`. */
  let wasUpdating = false;
  /** `editor.error`'s value as of the last time the refresh-loop effect ran
   * — used only to detect "a NEW rederive failure just arrived" so the
   * dismissible banner (`rederiveErrorDismissed`) reopens for it even if an
   * earlier failure had already been dismissed. */
  let lastRederiveError: string | null = null;
  /** Bumped at the START of every `refreshAfterEdit()` call — mirrors
   * `editor.ts`'s own `generation` guard (and `projects.ts`'s) exactly, for
   * the identical reason: `refreshAfterEdit()` is fired from a reactive
   * effect with no queue in front of it (unlike `editor.apply()`'s own
   * calls, which DO queue), so two rederives settling close together —
   * e.g. an edit followed by a fast Undo before the first refresh's
   * fetches/render have resolved — can have two `refreshAfterEdit()` calls
   * in flight at once. Both would otherwise write the same non-reactive
   * shared state (`score`, `cursorHandle`, `timeline`,
   * `lastTimelineIndex`, `performedNextCalls`) with no ordering
   * guarantee — whichever HTTP response or `loadMusicXml()` happens to
   * resolve LAST would "win" even if it started FIRST, leaving stale
   * notation/timeline. Every `refreshAfterEdit()` call captures the
   * generation value current at its own start; every checkpoint after an
   * `await` re-checks it and abandons (no further state writes) if a
   * newer call has since started — latest-wins, same as the two existing
   * examples. */
  let refreshGeneration = 0;

  function clampZoom(percent: number): number {
    return Math.min(MAX_ZOOM_PERCENT, Math.max(MIN_ZOOM_PERCENT, percent));
  }

  function onZoomChange(percent: number): void {
    zoomPercent = clampZoom(percent);
  }

  function onTabVisibleChange(visible: boolean): void {
    tabVisible = visible;
  }

  function toggleSidebar(): void {
    sidebarCollapsed = !sidebarCollapsed;
  }

  /** `note.halfTone` is real MIDI (middle C = 60) minus 12 (one octave) —
   * NOT the ready-made MIDI number it looks like. Determined empirically,
   * not from source-reading (two earlier readings of the installed 2.1.2
   * bundle's `Pitch` internals — one alleging a flat `+24` offset, one
   * alleging `.halfTone` was unreliable per-note — were both disproven by
   * live data; see task-6-report.md "MIDI pitch" for the full trail).
   * Confirmed against every note manually clicked in both task5-guitar and
   * task5-piano (17 notes total, 0 exceptions) by cross-referencing
   * `note.halfTone + 12` against the exported MusicXML's own `<step>`/
   * `<octave>` for that note and against `ScoreEvent.pitch` for the score
   * event it was expected to correlate to — e.g. task5-guitar's first
   * chord (`<step>E</step><octave>3</octave>` plus a `<chord/>`-tagged
   * `<step>E</step><octave>2</octave>`, i.e. real MIDI 52 and 40) read
   * `note.halfTone` as 40 and 28 respectively — each exactly 12 short. */
  const OSMD_HALFTONE_TO_MIDI_OFFSET = 12;

  function midiPitchOf(note: Note): number {
    return note.halfTone + OSMD_HALFTONE_TO_MIDI_OFFSET;
  }

  /** Walks the real, loaded OSMD cursor once from reset() to EndReached,
   * recording (a) which step indices are NOT rest-only steps, for
   * timeline.ts's `buildTimeline`, and (b) — extending the same walk, not a
   * second one (task-6-brief.md) — every non-rest note's pitch/staff/
   * graphical position at each such step, for correlate.ts's
   * `buildEventPositionIndex`. A step is rest-only when every
   * NotesUnderCursor() entry is a rest (or there are none) — confirmed rest
   * steps only ever appear as the MusicXML exporter's explicit gap-filling
   * (task-1b R2); the guitar TAB staff's duplicate per-staff notes never
   * affect this (both staves agree on isRest() for the same musical
   * instant). `notesUnderCursor()[i]` and `gNotesUnderCursor()[i]` are
   * index-aligned (verified: both iterate the exact same
   * `VoicesUnderCursor().Notes` array in the installed bundle — see
   * task-6-report.md), so zipping them by index is safe. Leaves the cursor
   * reset to the start when done, ready for playback. */
  function walkCursor(cursor: OSMDCursorHandle): { nonRestStepIndices: number[]; stepNotes: StepNoteInfo[] } {
    const nonRestStepIndices: number[] = [];
    const stepNotes: StepNoteInfo[] = [];
    cursor.reset();
    let step = 0;
    while (!cursor.isEndReached()) {
      const notes = cursor.notesUnderCursor();
      const gNotes = cursor.gNotesUnderCursor();
      const isRestStep = notes.length === 0 || notes.every((note) => note.isRest());
      if (!isRestStep) {
        nonRestStepIndices.push(step);
        const stepEntry: StepNoteInfo = { step, notes: [] };
        for (let i = 0; i < notes.length; i += 1) {
          const note = notes[i];
          if (note.isRest()) continue;
          const pos = gNotes[i].PositionAndShape.AbsolutePosition;
          stepEntry.notes.push({
            pitch: midiPitchOf(note),
            staffId: note.ParentStaff.Id,
            x: pos.x,
            y: pos.y,
          });
        }
        stepNotes.push(stepEntry);
      }
      step += 1;
      cursor.next();
    }
    cursor.reset();
    return { nonRestStepIndices, stepNotes };
  }

  /** Re-walks the cursor (see `walkCursor`) and re-runs correlate.ts against
   * the already-built `timeline`/`score` (stable across re-renders — only
   * the OSMD graphical layout, and therefore each note's unit position, can
   * change) to refresh `notation`'s position index, then re-applies
   * whatever is currently selected so the highlight follows the note to its
   * new position. No-op if the cursor/score aren't available, or if the
   * walk/correlate fails — click-to-select degrades silently rather than
   * throwing out of a re-render callback (the existing `syncError` state
   * already covers the load-time failure case; a rebuild failure here just
   * leaves the previous, possibly stale, positions in place). */
  function rebuildEventPositions(): void {
    if (!cursorHandle || !score) return;
    try {
      const { stepNotes } = walkCursor(cursorHandle);
      const positions = buildEventPositionIndex(stepNotes, timeline, score);
      notation?.setEventPositions(positions);
    } catch {
      // Leave whatever positions were already set — degrade gracefully.
    }
    notation?.highlightEvent($editor.selectedEventId);
  }

  /** IMPORTANT 2: `osmd.render()` (triggered by Notation's applyZoom()/
   * applyTabVisibility() on every zoom change or TAB-staff toggle)
   * constructs a brand-new OSMD Cursor internally — the previous one is
   * simply discarded, not updated in place. Notation's `getCursor()`
   * handle reads `osmd.cursor` fresh on every call (see its own comment),
   * so `cursorHandle` itself keeps driving whichever Cursor is current —
   * but a fresh Cursor's own state (visibility, iterator position) does
   * NOT carry over from the old one, so it must be explicitly re-shown and
   * walked back to the position that matches current playback: reset the
   * step-tracking bookkeeping to "nothing applied yet" and re-run
   * applyCursorForTime() for the current playback position. */
  function handleNotationRerender(): void {
    if (!cursorHandle) return;
    // Task 6: rebuild click-to-select positions (and reapply the current
    // highlight at its new position) BEFORE the playback-cursor resync below
    // — `rebuildEventPositions` performs its own full walk (`walkCursor`
    // itself resets/traverses/resets the cursor), and doing that first, then
    // letting the existing resync below move the cursor to where playback
    // actually is, keeps the two concerns (click-to-select positions vs.
    // playback cursor position) from interleaving their cursor.next() calls.
    rebuildEventPositions();
    cursorHandle.reset();
    cursorHandle.show();
    lastTimelineIndex = -1;
    performedNextCalls = 0;
    applyCursorForTime($playback.position);
  }

  function maybeScrollCursorIntoView(): void {
    const now = performance.now();
    if (now - lastScrollAt < SCROLL_THROTTLE_MS) return;
    lastScrollAt = now;
    cursorHandle?.cursorElement()?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  /** The single place that moves the OSMD cursor to match an audio time —
   * called directly from the rAF loop (while playing, already at most once
   * per frame) and, coalesced, from handleSeek() via scheduleCursorSync()
   * below (so dragging the scrubber moves the cursor even while paused). */
  function applyCursorForTime(t: number): void {
    if (!cursorHandle || timeline.length === 0) return;
    const idx = cursorIndexAt(timeline, t);
    if (idx === lastTimelineIndex) return;
    const desired = desiredNextCallsFor(timeline, idx);
    const plan = planCursorMove(performedNextCalls, desired);
    if (plan.reset) cursorHandle.reset();
    for (let i = 0; i < plan.nextCalls; i += 1) cursorHandle.next();
    performedNextCalls = desired;
    lastTimelineIndex = idx;
    maybeScrollCursorIntoView();
  }

  // Scrubbing fires the range input's `oninput` far faster than rAF ticks
  // during a fast drag — routing every one of those straight into
  // applyCursorForTime() means every event does its own reset()+N×next()
  // OSMD walk (cheap for a short clip, pathological for a long one, and
  // worse dragging backward since that path is always a full reset()).
  // The audio seek + position/time display must stay immediate (that's
  // what makes scrubbing feel responsive), but the OSMD cursor walk itself
  // only needs to reflect the LATEST scrub position, applied at most once
  // per animation frame — a textbook "latest-wins" coalescer.
  const scheduleCursorSync = createCoalescer<number>(applyCursorForTime, (flush) => {
    requestAnimationFrame(flush);
  });

  function handleSeek(t: number): void {
    playback.seek(t);
    scheduleCursorSync(t);
  }

  function tick(): void {
    rafId = requestAnimationFrame(tick);
    // Read through the active `PlaybackSource` (not the `<audio>` element
    // directly) so cursor/position sync works the same whether "recording"
    // or "synth" is selected — see playback.ts's `activeSourceTime()`.
    const t = playback.activeSourceTime();
    if (t === null) return;
    playback.syncPosition(t);
    applyCursorForTime(t);
    // The synth source has no native "ended" event to drive
    // handleAudioEnded() the way the `<audio>` element does — detect
    // reaching the end here instead, for either source.
    if ($playback.playing && $playback.duration > 0 && t >= $playback.duration) {
      playback.pause();
    }
  }

  function startLoop(): void {
    if (rafId !== null) return;
    rafId = requestAnimationFrame(tick);
  }

  function stopLoop(): void {
    if (rafId === null) return;
    cancelAnimationFrame(rafId);
    rafId = null;
  }

  $effect(() => {
    if ($playback.playing) startLoop();
    else stopLoop();
  });

  function handleAudioDurationChange(): void {
    if (!audioEl) return;
    playback.setDuration(Number.isFinite(audioEl.duration) ? audioEl.duration : 0);
  }

  function handleAudioEnded(): void {
    playback.pause();
  }

  /** Defaults to "guitar" for any unrecognized value — mirrors the
   * `tabAvailable` check below, which treats "guitar" as the known,
   * explicitly-handled case. */
  function resolveSynthInstrument(part: ScorePart | undefined): SynthInstrument {
    return part?.instrument === "piano" ? "piano" : "guitar";
  }

  /** CRITICAL 1(b): building the playback timeline and walking the OSMD
   * cursor is kept in its own try/catch, separate from the score
   * fetch/parse/render try/catch in `loadScore()` below. By this point the
   * score has already fetched and rendered successfully — a failure here
   * (e.g. `buildTimeline`'s count-mismatch guard) is a playback-sync-only
   * problem, not a "nothing usable rendered" problem, so it must not blank
   * the view the way `error` does. On failure, `cursorHandle`/`timeline`
   * are left at their already-reset empty state (set by the caller before
   * this runs) so playback code's `if (!cursorHandle...)` guards make
   * sync a safe no-op, and `syncError` drives the inline notice + disabled
   * transport play controls instead. */
  function trySyncPlaybackTimeline(score_: ScoreJson): void {
    try {
      const cursor = notation?.getCursor() ?? null;
      if (!cursor) return;
      const { nonRestStepIndices, stepNotes } = walkCursor(cursor);
      timeline = buildTimeline(score_, nonRestStepIndices);
      cursor.show();
      cursorHandle = cursor;
      // Click-to-select (Task 6) is a separate, non-fatal concern from
      // playback-cursor sync above: a failure here must not set `syncError`
      // (which disables the transport's playback controls) — it should just
      // leave `notation` with no known positions, so clicks find nothing and
      // clear the selection instead of throwing.
      try {
        const positions = buildEventPositionIndex(stepNotes, timeline, score_);
        notation?.setEventPositions(positions);
      } catch {
        // Click-to-select unavailable for this score; playback is unaffected.
      }
    } catch (err: unknown) {
      syncError = err instanceof Error ? err.message : String(err);
    }
  }

  /** Rebuilds `synthSource` from a fresh `ScoreJson`, dropping the previous
   * one — same dispose-then-recreate sequence `loadScore()` and `onDestroy`
   * already use. Kept as its own function since it now has two callers:
   * `loadScore()` (initial load) and the synth-follows-store `$effect`
   * below (every edit after that). */
  function rebuildSynthSource(score_: ScoreJson): void {
    playback.attachSource("synth", null);
    synthSource?.dispose();
    synthSource = createSynthSource(score_, resolveSynthInstrument(score_.parts[0]));
    playback.attachSource("synth", synthSource);
  }

  /** Task 7: "synth playback reflects an edited pitch immediately" — the
   * `editor` store's `score` is updated synchronously from `apply()`'s HTTP
   * response, well before the rederive job (which only affects
   * notation/fingering, not pitch/timing) finishes. Reacting to every
   * `editor.score` reference change (not just post-rederive ones) is what
   * makes an edited pitch audible on the very next Play, without waiting
   * for `refreshAfterEdit()`. `lastSynthScore` suppresses the redundant
   * rebuild `loadScore()`'s own explicit `rebuildSynthSource()` call would
   * otherwise cause here (both react to the same score object). */
  $effect(() => {
    const s = $editor.score;
    if (!s || s === lastSynthScore) return;
    lastSynthScore = s;
    if (!synthSource) return; // loadScore() hasn't created the initial one yet.
    rebuildSynthSource(s);
  });

  /** Task 7 Step 4 refresh loop: fires once per `editor.updating` true ->
   * false transition that lands with no `editor.error` — i.e. exactly when
   * a rederive job just finished successfully. The rederive worker
   * (workers/transcription/src/aura_worker/rederive.py) updates the head
   * revision's `score_json` in place AND rewrites the SAME `Export` rows'
   * `object_key` (never creates new ones), so this reuses `project`'s
   * already-known MusicXML export id rather than re-listing projects.
   *
   * Re-fetches BOTH the score JSON (`GET /score`, the same request
   * `loadScore()` makes) and the MusicXML text: the score JSON is what
   * carries the rederive's actual output (reassigned string/fret/hand for
   * every non-locked note — `apply()`'s own immediate response only ever
   * touches the ONE edited event, see score_schema/edits.py's per-op
   * branches), so without re-fetching it the Inspector/TAB would show
   * stale fingering for every other note after a lock-a-neighbor rederive.
   * That refetched score is written into the `editor` store via
   * `setScore()` and is what `trySyncPlaybackTimeline()` below reads —
   * "the store's edited score, not the refetch" (the brief's phrasing)
   * means the playback timeline's onset/duration timing comes from that
   * score object, never from re-parsing the just-loaded MusicXML text
   * (buildTimeline() never touches XML for timing regardless of caller).
   */
  async function refreshAfterEdit(): Promise<void> {
    // Generation guard (see `refreshGeneration`'s own comment for the full
    // race this closes) — captured unconditionally at the very start,
    // mirroring `editor.ts`'s `runOp` bumping its own `generation` before
    // doing anything else, even work that might fail/return early.
    refreshGeneration += 1;
    const myGeneration = refreshGeneration;

    const exportId = project?.exports.find((item) => item.format === "musicxml")?.id ?? null;
    if (!exportId) return;
    try {
      // `cache: "no-store"` on BOTH requests — confirmed necessary by hand
      // (not a defensive guess): the export URL is the SAME
      // `/v1/exports/{id}/download` for every rederive (the backend
      // rewrites that Export row's `object_key` in place rather than
      // minting a new export id — see rederive.py), and Starlette's
      // `FileResponse` sends no `Cache-Control` header at all. Without this,
      // the browser's heuristic HTTP cache served the PREVIOUS body for
      // that URL — Inspector fields (sourced from the JSON response, whose
      // own default caching this also fixes) showed the freshly-edited
      // fingering correctly while the rendered TAB staff kept showing the
      // stale one, since OSMD was faithfully rendering genuinely stale XML
      // text.
      const [freshScore, xmlText] = await Promise.all([
        fetch(api.scoreUrl(projectId), { cache: "no-store" }).then(async (resp) => {
          if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
          return resp.json() as Promise<ScoreJson>;
        }),
        fetch(api.exportDownloadUrl(exportId), { cache: "no-store" }).then(async (resp) => {
          if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
          return resp.text();
        }),
      ]);
      // First checkpoint: a newer refreshAfterEdit() may have started (and
      // possibly already finished) while these two fetches were in
      // flight. If so, this call's result is stale — abandon before
      // touching ANY shared state (not even `score`/`editor.setScore()`),
      // so it can never clobber what the newer call already wrote or is
      // about to write.
      if (myGeneration !== refreshGeneration) return;

      // Kept in lockstep with the ACTUALLY-rendered notation: `score` (the
      // local, non-store copy `rebuildEventPositions()`/zoom re-renders
      // read) must only advance once the MusicXML that describes it has
      // also been loaded — see the local `score` declaration's own note on
      // why this can't just track `editor.score` directly.
      score = freshScore;
      editor.setScore(freshScore);

      cursorHandle = null;
      timeline = [];
      lastTimelineIndex = -1;
      performedNextCalls = 0;
      await notation?.loadMusicXml(xmlText);
      // Second checkpoint: a newer refresh may have started (and written
      // its OWN score/cursorHandle/timeline) during the `loadMusicXml()`
      // await above — this call's `freshScore` is superseded, and calling
      // `trySyncPlaybackTimeline`/touching selection below would stomp the
      // newer call's state with older data.
      if (myGeneration !== refreshGeneration) return;

      trySyncPlaybackTimeline(freshScore);

      // A note deleted by the edit that triggered this refresh can no
      // longer be selected — clear rather than leaving a dangling id that
      // every position lookup below would just fail to find anyway.
      if ($editor.selectedEventId && !findEvent(freshScore, $editor.selectedEventId)) {
        editor.clearSelection();
      }
      notation?.highlightEvent($editor.selectedEventId);
    } catch (err: unknown) {
      // A stale call's failure must not overwrite a newer call's (possibly
      // already-successful) outcome with an error banner for a fetch/render
      // nobody cares about anymore.
      if (myGeneration !== refreshGeneration) return;
      // Non-fatal, same spirit as `syncError` elsewhere in this file: the
      // previously-rendered notation stays up rather than blanking the view.
      syncError = err instanceof Error ? err.message : String(err);
    }
  }

  // Refresh-loop trigger + rederive-failure banner state, in one effect
  // since both react to the same two `editor` fields.
  $effect(() => {
    const updating = $editor.updating;
    const err = $editor.error;
    if (err !== lastRederiveError) {
      lastRederiveError = err;
      if (err) rederiveErrorDismissed = false;
    }
    if (wasUpdating && !updating && !err) {
      void refreshAfterEdit();
    }
    wasUpdating = updating;
  });

  function dismissRederiveError(): void {
    rederiveErrorDismissed = true;
  }

  /** Retry for a failed rederive (Task 7 Step 4). A failed rederive job
   * leaves the head revision's user intent intact (the score edit itself
   * already committed — see rederive.py's ordering: the score write is
   * committed BEFORE the export-writing block that can fail) — Retry only
   * needs a FRESH rederive of that same head, and there is no dedicated
   * "retry rederive" endpoint. Implemented as the brief's documented
   * zero-backend-change trick: re-apply `set_locked` on any existing event
   * with its OWN current `locked` value. That's a semantically-null edit
   * (the resulting score is byte-for-byte identical to the current head)
   * but still goes through `apply_project_edit`'s ordinary
   * enqueue-a-rederive-job path, which is all a retry actually needs. (The
   * brief's alternative — a dedicated no-op `"touch"` op type in
   * `score_schema.edits` — would be cleaner but requires a backend change;
   * not taken, per the brief's own "implementer's choice" note.)
   */
  function retryRederive(): void {
    const currentScore = $editor.score;
    const eventId = firstEventId(currentScore);
    const found = findEvent(currentScore, eventId);
    if (!found) return;
    void editor.apply(projectId, { type: "set_locked", eventId: found.event.id, locked: found.event.locked });
  }

  // --- Task 7 Step 3: keyboard shortcuts -----------------------------------

  function isEditableTarget(target: EventTarget | null): boolean {
    if (!(target instanceof HTMLElement)) return false;
    const tag = target.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
  }

  /** Window-level so shortcuts work regardless of which element inside the
   * score view currently has focus, while still backing off completely
   * when focus is in a form control (`isEditableTarget`) — a Sidebar text
   * field must be able to use its own arrow keys/Delete normally. */
  function handleKeydown(event: KeyboardEvent): void {
    if (isEditableTarget(event.target)) return;

    if (event.key === "Escape") {
      editor.clearSelection();
      return;
    }

    // Ctrl+Z / Ctrl+Shift+Z (also accepting Cmd on macOS, since this is a
    // desktop app shell) — global, independent of selection.
    if ((event.ctrlKey || event.metaKey) && (event.key === "z" || event.key === "Z")) {
      event.preventDefault();
      if (event.shiftKey) void editor.redo(projectId);
      else void editor.undo(projectId);
      return;
    }

    const found = findEvent($editor.score, $editor.selectedEventId);
    if (!found) return;
    const ev = found.event;

    switch (event.key) {
      case "ArrowUp":
        event.preventDefault();
        void editor.apply(projectId, {
          type: "set_pitch",
          eventId: ev.id,
          pitch: clampPitch(ev.pitch + (event.shiftKey ? 12 : 1)),
        });
        break;
      case "ArrowDown":
        event.preventDefault();
        void editor.apply(projectId, {
          type: "set_pitch",
          eventId: ev.id,
          pitch: clampPitch(ev.pitch - (event.shiftKey ? 12 : 1)),
        });
        break;
      case "ArrowLeft":
        if (!part) break;
        event.preventDefault();
        void editor.apply(projectId, {
          type: "move_note",
          eventId: ev.id,
          notatedOnset: stepOnset(ev.notatedOnset, -1, part.meter),
        });
        break;
      case "ArrowRight":
        if (!part) break;
        event.preventDefault();
        void editor.apply(projectId, {
          type: "move_note",
          eventId: ev.id,
          notatedOnset: stepOnset(ev.notatedOnset, 1, part.meter),
        });
        break;
      // "Delete" is the standard key; "Backspace" is what macOS's
      // delete-key-in-the-backspace-position reports (its "Forward Delete"
      // reports "Delete") — accepting both is the beneficial deviation
      // from the brief's literal "Delete deletes" needed for that key to
      // actually work on a Mac keyboard.
      case "Delete":
      case "Backspace":
        event.preventDefault();
        void editor.apply(projectId, { type: "delete_note", eventId: ev.id });
        editor.clearSelection();
        break;
      default:
        break;
    }
  }

  async function loadScore(): Promise<void> {
    loading = true;
    error = null;
    syncError = null;
    cursorHandle = null;
    timeline = [];
    lastTimelineIndex = -1;
    performedNextCalls = 0;
    // A selection from a previous project (or a previous failed load) has no
    // meaning for whatever loads next.
    editor.clearSelection();
    playback.reset();
    if (synthSource) {
      playback.attachSource("synth", null);
      synthSource.dispose();
      synthSource = null;
    }
    try {
      const [projects, scoreJson] = await Promise.all([
        api.listProjects(),
        // `cache: "no-store"` — see refreshAfterEdit()'s note on why the
        // score/export URLs must never be served from the browser's HTTP
        // cache: their CONTENT changes (every edit rewrites the same
        // export id's underlying file) while their URL never does.
        fetch(api.scoreUrl(projectId), { cache: "no-store" }).then(async (resp) => {
          if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
          return resp.json() as Promise<ScoreJson>;
        }),
      ]);
      const found = projects.find((item) => item.id === projectId) ?? null;
      if (!found) throw new Error("Project not found.");
      const musicxmlExport = found.exports.find((item) => item.format === "musicxml");
      if (!musicxmlExport) throw new Error("No MusicXML export is available for this project yet.");
      const xmlResp = await fetch(api.exportDownloadUrl(musicxmlExport.id), { cache: "no-store" });
      if (!xmlResp.ok) throw new Error(`${xmlResp.status}: ${await xmlResp.text()}`);
      const xmlText = await xmlResp.text();

      project = found;
      score = scoreJson;
      tabVisible = true;
      zoomPercent = 100;

      synthSource = createSynthSource(scoreJson, resolveSynthInstrument(scoreJson.parts[0]));
      playback.attachSource("synth", synthSource);
      // Seeds the `editor` store with the just-loaded score AND records it
      // as the synth's own current source — the latter is what stops the
      // synth-follows-store `$effect` above from immediately rebuilding a
      // second, identical `synthSource` from the very score object this
      // just built one from.
      lastSynthScore = scoreJson;
      editor.setScore(scoreJson);

      await notation?.loadMusicXml(xmlText);
    } catch (err: unknown) {
      error = err instanceof Error ? err.message : String(err);
      loading = false;
      return;
    }
    // The score fetched and rendered successfully at this point — any
    // failure from here on is sync-only (see trySyncPlaybackTimeline) and
    // must not blank the view that just rendered fine.
    if (score) trySyncPlaybackTimeline(score);
    loading = false;
  }

  // IMPORTANT 3: the recording PlaybackSource must track whichever <audio>
  // element is CURRENTLY mounted, not just the one that existed at
  // onMount(). The <audio> element lives in the `{:else}` branch of the
  // error/loading template below — after a load failure, that branch (and
  // its <audio> element) is torn down, and a successful Retry mounts a
  // BRAND NEW <audio> element with a new `audioEl` binding. An onMount-only
  // attach would keep the playback store pointing at the original,
  // now-detached element forever after any Retry. Reading `audioEl` here
  // makes this effect re-run on every such mount/unmount, always attaching
  // (or, when the element goes away, detaching) the source that matches
  // reality. `attachSource` already fully replaces whatever was attached
  // before in one call, so there is no separate "detach then attach" step
  // needed here.
  $effect(() => {
    const el = audioEl;
    playback.attachSource("recording", el ? createAudioSource(el) : null);
  });

  // Single place responsible for "selection changed -> redraw highlight":
  // reacts to every source of a `selectedEventId` change (Notation's own
  // click handler included — it only calls `editor.select()`/
  // `clearSelection()`, never `highlightEvent()` directly) so Task 7's
  // sidebar can select a note the same way and get the same highlight for
  // free. Re-render-triggered position rebuilds (`rebuildEventPositions`)
  // reapply the highlight separately, since a rebuild can move the SAME
  // selected id to a new position without `selectedEventId` itself changing.
  $effect(() => {
    notation?.highlightEvent($editor.selectedEventId);
  });

  onMount(() => {
    // `editor` is a module-level singleton and this component is remounted
    // per project (the hash router swaps `ScoreView` instances via
    // `{#key projectId}` rather than reloading the page) — reset() clears
    // whatever the PREVIOUS project's session left behind
    // (updating/canUndo/canRedo/error/selectedEventId) and abandons any of
    // its still in-flight rederive polls, before loadScore() below seeds
    // the store with this project's own score. See editor.ts's reset() doc
    // comment and task-7-report.md's "editor store has no cross-project
    // reset" finding, which this closes.
    editor.reset();
    window.addEventListener("keydown", handleKeydown);
    void loadScore();
  });

  onDestroy(() => {
    window.removeEventListener("keydown", handleKeydown);
    stopLoop();
    scheduleCursorSync.cancel();
    playback.pause();
    playback.attachSource("recording", null);
    playback.attachSource("synth", null);
    synthSource?.dispose();
    synthSource = null;
    // Invalidates any in-flight rederive poll (see editor.ts's own comment
    // on `stop()`) so a late-resolving job from this project can never
    // write `updating`/`error` into the store after this view has gone.
    editor.stop();
  });

  // Task 7: derived from the `editor` store, not the local `score` state —
  // `editor.score` is updated immediately on every successful edit (see the
  // synth-follows-store effect above), while local `score` only advances
  // once the matching MusicXML has actually been re-rendered (see
  // `refreshAfterEdit`). Facts/Inspector should reflect the edit the
  // instant it's applied, same as synth playback does.
  let part = $derived($editor.score?.parts[0] ?? null);
  let tabAvailable = $derived(part?.instrument === "guitar");
</script>

<div class="page">
  <header class="topbar">
    <a class="back-link" href="#/">&larr; Projects</a>
    <h1 class="title">{project?.title ?? "Loading…"}</h1>
  </header>

  {#if error}
    <div class="error-panel">
      {error}
      <button type="button" class="retry-link" onclick={() => loadScore()}>Retry</button>
    </div>
  {:else}
    <div class="workspace">
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggleCollapse={toggleSidebar}
        projectTitle={project?.title ?? "score"}
        {projectId}
        {part}
        {tabAvailable}
        {tabVisible}
        {onTabVisibleChange}
        {zoomPercent}
        {onZoomChange}
        exports={project?.exports ?? []}
      />

      <main class="paper-area">
        {#if loading}
          <p class="loading-note">Loading score…</p>
        {/if}
        {#if syncError}
          <!-- CRITICAL 1(b): inline, non-blocking — the score above rendered
               fine; only cursor/playback sync failed. Never blanks the view. -->
          <p class="sync-notice" role="status">Playback sync unavailable for this score.</p>
        {/if}
        {#if $editor.error && !rederiveErrorDismissed}
          <!-- Task 7 Step 4: a rederive job failed. Distinct from
               `.error-panel` above (nothing here is "unusable" — the score
               still shows the last successful edit) and dismissible, unlike
               it. -->
          <div class="rederive-banner" role="alert">
            <span>{$editor.error}</span>
            <div class="rederive-banner-actions">
              <button type="button" class="rederive-retry" onclick={retryRederive}>Retry</button>
              <button type="button" class="rederive-dismiss" onclick={dismissRederiveError} aria-label="Dismiss">&times;</button>
            </div>
          </div>
        {/if}
        <div class="paper">
          {#if $editor.updating}
            <!-- Task 7 Step 4: subtle, non-blocking — the paper underneath
                 stays fully interactive while a rederive job is in flight. -->
            <div class="updating-hint" aria-live="polite">Updating notation…</div>
          {/if}
          <Notation
            bind:this={notation}
            zoom={zoomPercent / 100}
            {tabVisible}
            onRerender={handleNotationRerender}
          />
        </div>
      </main>
    </div>

    <audio
      bind:this={audioEl}
      src={api.audioUrl(projectId)}
      preload="metadata"
      class="sr-only-audio"
      ondurationchange={handleAudioDurationChange}
      onloadedmetadata={handleAudioDurationChange}
      onended={handleAudioEnded}
    ></audio>

    <Transport onSeek={handleSeek} playbackSyncAvailable={!syncError} />
  {/if}
</div>

<style>
  .page {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    background: var(--bg);
    color: var(--text);
  }

  .topbar {
    flex: none;
    display: flex;
    align-items: baseline;
    gap: 16px;
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
  }

  .back-link {
    color: var(--accent);
    font-size: 13px;
    text-decoration: none;
    flex: none;
  }

  .back-link:hover {
    text-decoration: underline;
  }

  .title {
    font-size: 15px;
    font-weight: 600;
    margin: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .error-panel {
    margin: 20px;
    background: rgba(224, 99, 99, 0.1);
    border: 1px solid rgba(224, 99, 99, 0.35);
    color: #e58a8a;
    border-radius: 9px;
    padding: 12px 16px;
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .retry-link {
    background: none;
    border: 1px solid currentColor;
    color: inherit;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    cursor: pointer;
  }

  .workspace {
    flex: 1;
    display: flex;
    min-height: 0;
  }

  .paper-area {
    flex: 1;
    min-width: 0;
    overflow: auto;
    padding: 32px clamp(16px, 4vw, 56px);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
  }

  .loading-note {
    margin: 0;
    font-size: 13px;
    color: var(--dim);
  }

  /* CRITICAL 1(b): deliberately subdued/inline, unlike .error-panel — this
   * notice must read as "one feature is degraded", not "something broke". */
  .sync-notice {
    margin: 0;
    max-width: 900px;
    width: 100%;
    box-sizing: border-box;
    background: rgba(224, 99, 99, 0.08);
    border: 1px solid rgba(224, 99, 99, 0.25);
    color: var(--dim);
    border-radius: 8px;
    padding: 8px 14px;
    font-size: 12px;
  }

  .paper {
    position: relative;
    background: var(--paper);
    color: #1e1d21;
    border-radius: 10px;
    box-shadow: 0 8px 28px rgba(0, 0, 0, 0.35);
    padding: 32px clamp(16px, 4vw, 48px);
    width: 100%;
    max-width: 900px;
  }

  /* Task 7 Step 4: a small, dim pill in the corner of the paper — never a
   * full-paper overlay, since the score underneath must stay fully
   * readable/interactive while a rederive job is running. */
  .updating-hint {
    position: absolute;
    top: 10px;
    right: 10px;
    background: rgba(30, 29, 33, 0.85);
    color: var(--dim);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 4px 12px;
    font-size: 11px;
    pointer-events: none;
    z-index: 1;
  }

  /* Task 7 Step 4: distinct from `.error-panel` (that one blanks the whole
   * view) and `.sync-notice` (that one has no action) — this is dismissible
   * and offers Retry, matching a rederive failure's actual recovery path. */
  .rederive-banner {
    margin: 0;
    max-width: 900px;
    width: 100%;
    box-sizing: border-box;
    background: rgba(224, 99, 99, 0.1);
    border: 1px solid rgba(224, 99, 99, 0.35);
    color: #e58a8a;
    border-radius: 9px;
    padding: 10px 14px;
    font-size: 13px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .rederive-banner-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: none;
  }

  .rederive-retry {
    background: none;
    border: 1px solid currentColor;
    color: inherit;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    cursor: pointer;
  }

  .rederive-dismiss {
    background: none;
    border: none;
    color: inherit;
    font-size: 16px;
    line-height: 1;
    cursor: pointer;
    padding: 2px 4px;
  }

  .sr-only-audio {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
  }
</style>
