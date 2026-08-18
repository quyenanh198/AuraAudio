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
