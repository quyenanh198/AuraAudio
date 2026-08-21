/** Detection-quality roadmap item 3: the opt-in "isolate instrument from
 * mix" source-separation toggle. Guitar only, evidence-backed -- see
 * docs/benchmarks/2026-08-21-dq3.md's "Stem-mapping decision" section for
 * why (htdemucs_6s's own piano stem is unreliable, matching upstream's own
 * documented caveat). The backend already treats a piano project with the
 * setting enabled as a harmless no-op (aura_worker.runner's instrument
 * gate) -- this module is the single source of truth the frontend uses to
 * make that no-op VISIBLE instead of silent, per the code-review finding
 * that the checkbox rendered before the instrument was chosen and gave no
 * feedback when a user picked Piano with it checked. */

export type SeparationInstrument = "guitar" | "piano";

/** Whether the "isolate instrument from mix" setting has any effect for
 * `instrument`. Mirrors aura_worker.runner.run_transcription_job's own
 * `instrument == "guitar"` gate exactly -- keep both in sync if this ever
 * changes. */
export function separationAppliesToInstrument(instrument: SeparationInstrument): boolean {
  return instrument === "guitar";
}

/** A short, user-facing note for the case where `separateSource` is
 * checked but `instrument` won't actually use it (currently: Piano).
 * Returns null when there is nothing to warn about (unchecked, or an
 * instrument the setting does apply to) -- callers should render nothing
 * in that case, not an empty note. */
export function separationNoopNote(
  separateSource: boolean,
  instrument: SeparationInstrument,
): string | null {
  if (!separateSource || separationAppliesToInstrument(instrument)) return null;
  return "Won't apply to Piano — Isolate instrument from mix only works for Guitar right now.";
}
