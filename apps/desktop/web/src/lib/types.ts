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
