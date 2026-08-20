import type { EditOp, EditResponse, ImportYoutubeResponse, ProjectListItem, SystemDepsResponse } from "./types";

// Fixed dev/desktop backend port — apps/desktop/run_backend.py:43
// (`AURA_BACKEND_PORT = 8317`), bound to 127.0.0.1 only.
export const BASE = "http://127.0.0.1:8317";

async function json<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
  return resp.json() as Promise<T>;
}

/** Thrown by the edit endpoints instead of a plain `Error` so callers (the
 * `editor` store) can branch on the HTTP status — a 409 at undo/redo bounds
 * is an expected, recoverable condition, not a surfaced error message. */
export class EditApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "EditApiError";
    this.status = status;
  }
}

function hasStringDetail(body: unknown): body is { detail: string } {
  return (
    typeof body === "object" &&
    body !== null &&
    "detail" in body &&
    typeof (body as { detail: unknown }).detail === "string"
  );
}

/** Like `json<T>`, but on failure raises `EditApiError` carrying the HTTP
 * status plus, where present, FastAPI's `{"detail": "..."}` body verbatim
 * (apps/api/src/aura_api/routers/edits.py raises `HTTPException(status_code,
 * detail=...)` for every error case: 404 project-not-found, 422 invalid op —
 * human-readable `reason` from `EditError` — and 409 at undo/redo/revert
 * bounds) instead of the raw `"<status>: <body-text>"` string `json<T>`
 * produces. That keeps the 422 `reason` directly usable as a user-facing
 * message, and keeps the 409 status readable by callers without re-parsing
 * the message. */
async function editJson<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text();
    let detail: string | undefined;
    try {
      const body: unknown = JSON.parse(text);
      if (hasStringDetail(body)) detail = body.detail;
    } catch {
      // Not JSON — fall through to the generic message below.
    }
    throw new EditApiError(resp.status, detail ?? `${resp.status}: ${text}`);
  }
  return resp.json() as Promise<T>;
}

/** Thrown by POST /v1/imports/youtube instead of a plain `Error`, so
 * callers can branch on `code` (e.g. `"yt_dlp_not_found"` for the 409 --
 * apps/api/src/aura_api/routers/imports.py raises that with a
 * `{"code": ..., "message": ...}` detail specifically so the frontend
 * doesn't have to string-match) without re-parsing the response body. */
export class ImportApiError extends Error {
  readonly status: number;

  readonly code: string | null;

  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.name = "ImportApiError";
    this.status = status;
    this.code = code;
  }
}

function hasDetail(body: unknown): body is { detail: unknown } {
  return typeof body === "object" && body !== null && "detail" in body;
}

/** Like `json<T>`, but on failure raises `ImportApiError`. The imports
 * endpoint's `detail` is either a plain string (422 validation, 502
 * yt-dlp failures) or a `{code, message}` object (409 yt-dlp-missing) --
 * this normalizes both into a human-readable `message` plus an optional
 * machine-readable `code`. */
async function importJson<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text();
    let message = `${resp.status}: ${text}`;
    let code: string | null = null;
    try {
      const body: unknown = JSON.parse(text);
      if (hasDetail(body)) {
        const detail = body.detail;
        if (typeof detail === "string") {
          message = detail;
        } else if (typeof detail === "object" && detail !== null) {
          const d = detail as { message?: unknown; code?: unknown };
          if (typeof d.message === "string") message = d.message;
          if (typeof d.code === "string") code = d.code;
        }
      }
    } catch {
      // Not JSON -- fall through to the generic message below.
    }
    throw new ImportApiError(resp.status, message, code);
  }
  return resp.json() as Promise<T>;
}

// Response shapes below are verified against apps/api/src/aura_api/schemas.py
// (see task-5-report.md for the exact file:line for each) rather than
// assumed from the plan — the plan explicitly calls this out as a
// verify-against-reality step.

/** apps/api/src/aura_api/schemas.py ProjectResponse (id, title, instrument, media_asset_id). */
export interface CreateProjectResponse {
  id: string;
  title: string;
  instrument: string;
  media_asset_id: string;
}

/** apps/api/src/aura_api/schemas.py CreateJobResponse (job_id, status). */
export interface CreateJobResponse {
  job_id: string;
  status: string;
}

/** apps/api/src/aura_api/schemas.py JobStatusResponse. */
export interface JobStatusResponse {
  id: string;
  status: string;
  stage: string | null;
  progress: number;
  error_code: string | null;
  error_detail: string | null;
}

export const api = {
  listProjects: () => fetch(`${BASE}/v1/projects`).then((r) => json<ProjectListItem[]>(r)),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return fetch(`${BASE}/v1/uploads`, { method: "POST", body: form }).then((r) =>
      json<{ object_key: string }>(r),
    );
  },
  createProject: (title: string, instrument: string, object_key: string) =>
    fetch(`${BASE}/v1/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, instrument, object_key }),
    }).then((r) => json<CreateProjectResponse>(r)),
  startTranscription: (projectId: string) =>
    fetch(`${BASE}/v1/projects/${projectId}/transcriptions`, { method: "POST" }).then((r) =>
      json<CreateJobResponse>(r),
    ),
  getJob: (jobId: string) => fetch(`${BASE}/v1/jobs/${jobId}`).then((r) => json<JobStatusResponse>(r)),
  scoreUrl: (projectId: string) => `${BASE}/v1/projects/${projectId}/score`,
  audioUrl: (projectId: string) => `${BASE}/v1/projects/${projectId}/audio`,
  exportDownloadUrl: (exportId: string) => `${BASE}/v1/exports/${exportId}/download`,
  applyEdit: (projectId: string, op: EditOp) =>
    fetch(`${BASE}/v1/projects/${projectId}/edits`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(op),
    }).then((r) => editJson<EditResponse>(r)),
  undoEdit: (projectId: string) =>
    fetch(`${BASE}/v1/projects/${projectId}/edits/undo`, { method: "POST" }).then((r) =>
      editJson<EditResponse>(r),
    ),
  redoEdit: (projectId: string) =>
    fetch(`${BASE}/v1/projects/${projectId}/edits/redo`, { method: "POST" }).then((r) =>
      editJson<EditResponse>(r),
    ),
  revertEdits: (projectId: string) =>
    fetch(`${BASE}/v1/projects/${projectId}/edits/revert`, { method: "POST" }).then((r) =>
      editJson<EditResponse>(r),
    ),
  // `cache: "no-store"` — same reasoning as every other mutable-content
  // fetch in this file: the binaries on a user's PATH can change between
  // checks (e.g. right after the user runs the suggested install command),
  // so a cached 200 must never mask that.
  getSystemDeps: () =>
    fetch(`${BASE}/v1/system/deps`, { cache: "no-store" }).then((r) => json<SystemDepsResponse>(r)),
  importYoutube: (url: string) =>
    fetch(`${BASE}/v1/imports/youtube`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    }).then((r) => importJson<ImportYoutubeResponse>(r)),
};
