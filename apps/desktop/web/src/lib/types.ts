// Mirrors apps/api/src/aura_api/schemas.py verbatim (verified field-by-field
// against the real backend — see task-5-report.md for file:line references).

export interface ProjectJobSummary {
  id: string;
  status: string;
  stage: string | null;
  progress: number;
}

export interface ProjectExportSummary {
  id: string;
  format: string;
}

export interface ProjectListItem {
  id: string;
  title: string;
  instrument: string;
  created_at: string;
  duration_ms: number | null;
  job: ProjectJobSummary | null;
  exports: ProjectExportSummary[];
}

/** Statuses a TranscriptionJob can report; anything outside
 * `succeeded`/`failed` is treated as still-running for polling purposes. */
export type JobStatus = string;

export const TERMINAL_JOB_STATUSES: ReadonlySet<JobStatus> = new Set(["succeeded", "failed"]);

// Score JSON (schemaVersion 4) — mirrors packages/score_schema/src/score_schema/{models,validate}.py
// verbatim (verified field-by-field against the real schema, not assumed from memory).
// GET /v1/projects/{id}/score (apps/api/src/aura_api/routers/scores.py::get_score) returns this
// shape directly (json.loads of the stored "assign" stage artifact, no envelope).

export interface ScoreConfidence {
  tempo: number;
  meter: number;
  key: number;
}

/** One transcribed note/rest event. `string`/`fret` are guitar-only (absent or null for piano);
 * `hand` is piano-only ("left" | "right", absent or null for guitar). Not needed by Task 6's UI
 * beyond typing the score shape, but kept complete since Task 7 (timeline sync) consumes
 * `onsetSeconds` from this same type. */
export interface ScoreEvent {
  id: string;
  pitch: number;
  onsetSeconds: number;
  offsetSeconds: number;
  notatedOnset: string;
  notatedDuration: string;
  voice: number;
  confidence: number;
  locked: boolean;
  string?: number | null;
  fret?: number | null;
  hand?: "left" | "right" | null;
}

export interface ScoreMeasure {
  number: number;
  events: ScoreEvent[];
}

export interface ScorePart {
  instrument: string;
  tempoBpm: number;
  meter: string;
  key: string;
  confidence: ScoreConfidence;
  measures: ScoreMeasure[];
}

export interface ScoreTimeMapEntry {
  beat: number;
  seconds: number;
}

export interface ScoreJson {
  schemaVersion: number;
  timeMap: ScoreTimeMapEntry[];
  parts: ScorePart[];
}

// Edit ops — discriminated union on `type`, mirroring the op table verbatim
// from packages/score_schema/src/score_schema/edits.py::apply_edit (the
// `kind == "..."` branches and each branch's `_require(op, "...")` /
// `op.get(...)` reads) rather than the plan prose. `voice` on `add_note` is
// optional there too — the backend defaults it via `op.get("voice", 1)`.
export interface SetPitchOp {
  type: "set_pitch";
  eventId: string;
  pitch: number;
}

export interface MoveNoteOp {
  type: "move_note";
  eventId: string;
  notatedOnset: string;
}

export interface SetDurationOp {
  type: "set_duration";
  eventId: string;
  notatedDuration: string;
}

export interface DeleteNoteOp {
  type: "delete_note";
  eventId: string;
}

export interface AddNoteOp {
  type: "add_note";
  measureNumber: number;
  notatedOnset: string;
  notatedDuration: string;
  pitch: number;
  voice?: number;
}

export interface SetFingeringOp {
  type: "set_fingering";
  eventId: string;
  string: number;
  fret: number;
}

export interface SetHandOp {
  type: "set_hand";
  eventId: string;
  hand: "left" | "right";
}

export interface SetLockedOp {
  type: "set_locked";
  eventId: string;
  locked: boolean;
}

/** `field`/`value` pairing mirrors the `set_part_fact` branch's three
 * accepted fields (`tempoBpm`: number, `meter`: string, `key`: string) —
 * kept as a single `string | number` rather than three further-discriminated
 * variants since the backend itself dispatches on the runtime `field` string,
 * not on a TS-visible type. */
export interface SetPartFactOp {
  type: "set_part_fact";
  field: string;
  value: string | number;
}

export type EditOp =
  | SetPitchOp
  | MoveNoteOp
  | SetDurationOp
  | DeleteNoteOp
  | AddNoteOp
  | SetFingeringOp
  | SetHandOp
  | SetLockedOp
  | SetPartFactOp;

/** apps/api/src/aura_api/routers/edits.py::_respond — the common 200 shape
 * shared by POST /edits, /edits/undo, /edits/redo, /edits/revert. */
export interface EditResponse {
  version: number;
  score: ScoreJson;
  rederive_job_id: string;
}
